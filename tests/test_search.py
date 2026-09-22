"""Search selection, isolation, concurrency and crash recovery invariants."""
import json
import os
import re
import threading
import time

import pytest

from fpga_lab.search import (SearchConfig, SearchProblem, apply_edits, eligible,
                             balanced_budgets, candidate_report, pareto_front, resource_caps, run_search)
from fpga_lab.vhdl import Proposal


class Designs:
    model = 'test-provider'

    def __init__(self, costs):
        self.costs, self.calls = costs, []

    def request(self, prompt, folder, schema):
        worker, iteration = map(int, re.search(r'SEARCH worker (\d+), round (\d+)', prompt).groups())
        self.calls.append((worker, iteration))
        source = prompt.split('COMPLETE BASE CANDIDATE:\n')[1].split('\nBASE DEVELOPMENT FEEDBACK:')[0]
        old = re.search(r'COST=\d+', source).group()
        response = {'edits': [{'old': old, 'new': f'COST={self.costs[worker-1][iteration-1]}'}],
                    'latency': 1, 'notes': 'Unit-test candidate'}
        (folder/'response.json').write_text(json.dumps(response))
        return response

    def propose(self, prompt, folder):
        self.calls.append('fresh')
        return Proposal('COST=50', 1)


def harness(check=None):
    seen = []

    def evaluate(proposal, folder, audit):
        cost = int(re.search(r'COST=(\d+)', proposal.vhdl).group(1))
        seen.append((folder.name, cost, audit))
        if check:
            check(proposal, folder, audit)
        return {'accepted': cost != 1, 'latency': proposal.latency,
                'hardware': {'luts': cost, 'ffs': 10, 'dsps': 0, 'brams': 0}}

    return SearchProblem('Finite test contract', {'kind': 'test'}, evaluate), seen


def search(problem, out, provider, **kwargs):
    return run_search(problem, out, provider=provider, environment={'image_id': 'test'},
                      progress=lambda _: None, **kwargs)


def test_search_continues_after_passing_and_keeps_best_before_audit(tmp_path):
    problem, seen = harness()
    provider = Designs([[80, 1, 90]])
    result = search(problem, tmp_path, provider, seed=Proposal('COST=100', 1),
                    config=SearchConfig(workers=1, rounds=3))
    assert provider.calls == [(1, 1), (1, 2), (1, 3)]
    assert result['selected'] == 'worker-01/round-01'
    assert result['improvement'] == {'before': 100, 'after': 80, 'reduction': 20, 'percent': 20}
    assert [cost for _, cost, audit in seen if audit] == [80]
    assert (tmp_path/'final/candidate.vhd').read_text() == 'COST=80'
    board = json.loads((tmp_path/'leaderboard.json').read_text())
    assert len(board['candidates']) == 4
    assert 'worker-01/round-02' not in board['ranking']


def test_no_passing_candidate_is_never_audited(tmp_path):
    problem, seen = harness()
    provider = Designs([[1]])
    result = search(problem, tmp_path, provider, seed=Proposal('COST=1', 1),
                    config=SearchConfig(workers=1, rounds=1))
    assert result['status'] == 'no_passing_candidate'
    assert provider.calls == [(1, 1)]  # Even a failing baseline supplies source.
    assert not any(audit for _, _, audit in seen)
    assert not (tmp_path/'selection.json').exists()


def test_resume_reuses_proposals_and_completed_evaluations(tmp_path):
    interrupted = False

    def fail_once(proposal, folder, audit):
        nonlocal interrupted
        if folder.name == 'round-02' and not interrupted:
            interrupted = True
            raise KeyboardInterrupt()

    problem, seen = harness(fail_once)
    provider = Designs([[80, 70]])
    options = {'seed': Proposal('COST=100', 1), 'config': SearchConfig(workers=1, rounds=2)}
    with pytest.raises(KeyboardInterrupt):
        search(problem, tmp_path, provider, **options)
    assert json.loads((tmp_path/'summary.json').read_text())['status'] == 'interrupted'
    result = search(problem, tmp_path, provider, resume=True, **options)
    assert result['accepted']
    assert provider.calls == [(1, 1), (1, 2)]
    assert len([x for x in seen if x[0] == 'baseline']) == 1
    assert len([x for x in seen if x[0] == 'round-01']) == 1
    assert len([x for x in seen if x[0] == 'round-02']) == 2
    before = list(seen)
    assert search(problem, tmp_path, provider, resume=True, **options) == result
    assert seen == before


def test_audit_failure_is_terminal_and_selection_precedes_audit(tmp_path):
    problem, seen = harness()
    evaluate = problem.evaluate

    def reject_audit(proposal, folder, audit):
        if audit:
            assert (tmp_path/'selection.json').exists()
        result = evaluate(proposal, folder, audit)
        if audit:
            result.update(accepted=False, error='Held-out failure must stay out of prompts')
        return result

    problem.evaluate = reject_audit
    provider = Designs([[80, 70]])
    options = {'seed': Proposal('COST=100', 1), 'config': SearchConfig(workers=1, rounds=2)}
    result = search(problem, tmp_path, provider, **options)
    assert result['status'] == 'audit_failed'
    assert result['selected'] == 'worker-01/round-02'
    before = list(seen)
    assert search(problem, tmp_path, provider, resume=True, **options) == result
    assert seen == before
    assert provider.calls == [(1, 1), (1, 2)]
    for prompt in tmp_path.glob('worker-*/round-*/prompt.txt'):
        assert 'Held-out failure' not in prompt.read_text()
    with pytest.raises(ValueError, match='matching'):
        search(problem, tmp_path, provider, resume=True, seed=options['seed'],
               config=SearchConfig(workers=1, rounds=3))


def test_changed_artifact_or_identity_cannot_reuse_success(tmp_path):
    problem, _ = harness()
    provider = Designs([[80]])
    options = {'seed': Proposal('COST=100', 1), 'config': SearchConfig(workers=1, rounds=1)}
    search(problem, tmp_path, provider, **options)
    problem.identity = {'kind': 'changed'}
    with pytest.raises(ValueError, match='matching'):
        search(problem, tmp_path, provider, resume=True, **options)
    problem.identity = {'kind': 'test'}
    (tmp_path/'worker-01/round-01/candidate.vhd').write_text('COST=20')
    with pytest.raises(ValueError, match='artifact changed'):
        search(problem, tmp_path, provider, resume=True, **options)


def test_parallel_design_workers_share_hardware_limit(tmp_path):
    barrier = threading.Barrier(2)
    state = {'active': 0, 'peak': 0}
    lock = threading.Lock()
    problem, _ = harness()
    evaluate = problem.evaluate

    def limited(proposal, folder, audit):
        with lock:
            state['active'] += 1
            state['peak'] = max(state['peak'], state['active'])
        time.sleep(0.01)
        result = evaluate(proposal, folder, audit)
        with lock:
            state['active'] -= 1
        return result

    class ParallelDesigns(Designs):
        def request(self, *args):
            barrier.wait(timeout=5)  # Proves generation is concurrent even with tool_jobs=1.
            return super().request(*args)

    problem.evaluate = limited
    result = search(problem, tmp_path, ParallelDesigns([[80], [70]]),
                    seed=Proposal('COST=100', 1), config=SearchConfig(workers=2, rounds=1, tool_jobs=1))
    assert result['selected'] == 'worker-02/round-01'
    assert state['peak'] == 1


def test_fresh_generation_without_seed(tmp_path):
    problem, _ = harness()
    provider = Designs([])
    result = search(problem, tmp_path, provider, config=SearchConfig(workers=1, rounds=1))
    assert result['accepted'] and result['baseline'] is None
    assert provider.calls == ['fresh']


def test_saved_model_response_survives_interruption_before_proposal_save(tmp_path):
    class InterruptedProvider(Designs):
        def request(self, *args):
            super().request(*args)  # The actual provider also saves response.json first.
            raise KeyboardInterrupt()

    problem, seen = harness()
    provider = InterruptedProvider([[70]])
    options = {'seed': Proposal('COST=100', 1), 'config': SearchConfig(workers=1, rounds=1)}
    with pytest.raises(KeyboardInterrupt):
        search(problem, tmp_path, provider, **options)
    assert not (tmp_path/'worker-01/round-01/proposal.json').exists()
    result = search(problem, tmp_path, provider, resume=True, **options)
    assert result['accepted'] and result['improvement']['after'] == 70
    assert provider.calls == [(1, 1)]
    assert len([x for x in seen if x[0] == 'baseline']) == 1


def test_selected_objective_can_trade_luts_for_registers(tmp_path):
    problem, _ = harness()
    evaluate = problem.evaluate

    def resources(proposal, folder, audit):
        result = evaluate(proposal, folder, audit)
        cost = result['hardware']['luts']
        result['hardware'].update(luts=200-cost, ffs=cost)
        return result

    problem.evaluate = resources
    result = search(problem, tmp_path, Designs([[80, 70]]), seed=Proposal('COST=100', 1),
                    config=SearchConfig(workers=1, rounds=2, objective='ffs', allow_resource_tradeoffs=True))
    assert result['improvement']['reduction'] == 30
    assert result['development']['hardware']['luts'] == 130
    assert result['selected'] == 'worker-01/round-02'


def test_provider_errors_consume_budget_without_losing_baseline(tmp_path):
    class FailedProvider(Designs):
        def request(self, *args):
            self.calls.append('failure')
            raise RuntimeError('Provider unavailable')

    problem, _ = harness()
    provider = FailedProvider([])
    result = search(problem, tmp_path, provider, seed=Proposal('COST=100', 1),
                    config=SearchConfig(workers=1, rounds=2))
    assert result['accepted'] and result['selected'] == 'baseline'
    assert result['improvement']['reduction'] == 0
    assert provider.calls == ['failure', 'failure']
    board = json.loads((tmp_path/'leaderboard.json').read_text())
    assert len([r for r in board['candidates'] if r['error'] == 'Provider unavailable']) == 2


def test_second_process_cannot_own_the_same_search(tmp_path):
    import fcntl

    problem, _ = harness()
    with (tmp_path/'.search.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match='Another search'):
            search(problem, tmp_path, Designs([]))


def test_prompts_preserve_complete_long_source_and_use_distinct_strategies(tmp_path):
    source = 'preserved prefix\n'*1000 + 'COST=100\n' + 'preserved suffix\n'*1000
    problem, _ = harness()
    search(problem, tmp_path, Designs([[80], [70]]), seed=Proposal(source, 1),
           config=SearchConfig(workers=2, rounds=1))
    one = (tmp_path/'worker-01/round-01/prompt.txt').read_text()
    two = (tmp_path/'worker-02/round-01/prompt.txt').read_text()
    assert source in one and source in two
    assert 'Reduce state and storage cost' in one
    assert 'Reduce arithmetic cost' in two


def test_complete_source_and_exact_edit_validation():
    source = 'prefix\n' * 10000 + 'COST=100\n' + 'suffix\n' * 10000
    result = apply_edits(Proposal(source, 1), {'edits': [{'old': 'COST=100', 'new': 'COST=80'}], 'latency': 1})
    assert result.vhdl == source.replace('COST=100', 'COST=80')
    for old in ('missing', '', 'prefix'):
        with pytest.raises(ValueError, match='exactly once'):
            apply_edits(Proposal(source, 1), {'edits': [{'old': old, 'new': 'x'}], 'latency': 1})


def test_unmeasured_or_invalid_resources_are_not_eligible():
    assert not eligible({'accepted': True}, 'luts')
    assert not eligible({'accepted': True, 'hardware': {'luts': 1, 'ffs': 0, 'dsps': 0, 'brams': True}}, 'luts')
    assert eligible({'accepted': True, 'hardware': {'luts': 1, 'ffs': 0, 'dsps': 0, 'bram18_equivalents': 2}}, 'brams')


@pytest.mark.parametrize('options', [{'workers': 0}, {'rounds': 51}, {'tool_jobs': 0}, {'objective': 'power'},
                                    {'max_dsps': -1}, {'max_luts': True}, {'max_brams': 1.5},
                                    {'allow_resource_tradeoffs': 'yes'}])
def test_invalid_search_configuration(options):
    with pytest.raises(ValueError):
        SearchConfig(**options)


def wifi_measurements():
    """Replay measured development resources, not hardware execution or a new audit."""
    from fpga_lab.problem import ROOT
    saved = json.loads((ROOT/'docs/wifi-search-results.json').read_text())
    return [{'id': row['id'], 'parent': row['parent'],
             'result': {'accepted': row['accepted'], 'hardware': row['hardware']}}
            for row in saved['candidates']]


@pytest.mark.parametrize('options,winner', [
    ({}, 'worker-02/round-01'),
    ({'max_dsps': 100}, 'worker-02/round-01'),
    ({'max_dsps': 135}, 'worker-02/round-02'),
    ({'allow_resource_tradeoffs': True}, 'worker-02/round-02'),
    ({'allow_resource_tradeoffs': True, 'max_dsps': 71}, 'worker-02/round-01'),
])
def test_recorded_wifi_tradeoffs_are_visible_and_respect_policy(options, winner):
    records = wifi_measurements()
    report = candidate_report(records, SearchConfig(**options), records[0]['result'])
    assert report['best'] == winner
    assert set(report['pareto']) == {'worker-02/round-01', 'worker-02/round-02'}
    assert report['verification_scope'] == 'development_only'
    row = next(r for r in report['candidates'] if r['id'] == 'worker-02/round-02')
    assert row['verified']
    assert row['resource_changes']['dsps']['delta'] == 64
    assert row['resource_changes']['luts']['delta'] == -8014
    if winner.endswith('round-01'):
        assert not row['accepted']
        assert row['constraint_violations']['dsps']['measured'] == 135


@pytest.mark.parametrize('dsp_budget,winner', [(740, 'worker-02/round-02'),
                                             (135, 'worker-02/round-01'),
                                             (100, 'worker-02/round-01'),
                                             (71, 'worker-02/round-01')])
def test_balanced_wifi_selection_accounts_for_available_dsp_budget(dsp_budget, winner):
    records = wifi_measurements()
    limits = {'luts': 134600, 'ffs': 269200, 'dsps': 740, 'brams': 730}
    report = candidate_report(records, SearchConfig(objective='balanced', max_dsps=dsp_budget),
                              records[0]['result'], limits)
    assert report['best'] == winner
    assert not report['baseline_resource_protection']
    assert len(report['pareto']) == 2
    best = next(row for row in report['candidates'] if row['id'] == winner)
    assert best['balanced_score']['budgets']['dsps'] == dsp_budget
    assert best['balanced_score']['peak_utilization'] <= 1
    if dsp_budget == 740:
        assert best['balanced_score']['limiting_resources'] == ['luts']
        assert best['balanced_score']['utilization']['dsps'] == pytest.approx(135/740)


def test_balanced_breaks_equal_bottlenecks_using_total_utilization():
    records = [{'id': name, 'result': {'accepted': True, 'hardware': hw}}
               for name, hw in [('a', {'luts': 50, 'ffs': 10, 'dsps': 40, 'brams': 0}),
                                ('b', {'luts': 50, 'ffs': 30, 'dsps': 5, 'brams': 0})]]
    report = candidate_report(records, SearchConfig(objective='balanced'), records[0]['result'],
                              {'luts': 100, 'ffs': 100, 'dsps': 100, 'brams': 0})
    assert report['best'] == 'b'
    assert set(report['pareto']) == {'a', 'b'}
    assert [r['balanced_score']['peak_utilization'] for r in report['candidates']] == [0.5, 0.5]
    assert report['candidates'][1]['balanced_score']['total_utilization'] == 0.85


def test_balanced_requires_budgets_and_never_relaxes_target_limits(tmp_path):
    problem, seen = harness()
    provider = Designs([[80]])
    with pytest.raises(ValueError, match='balanced requires a resource budget'):
        search(problem, tmp_path, provider, config=SearchConfig(objective='balanced'))
    assert not provider.calls and not seen
    limits = {'luts': 100, 'ffs': 50, 'dsps': 10, 'brams': 0}
    assert balanced_budgets(SearchConfig(objective='balanced', max_dsps=99), limits) == limits


def test_balanced_zero_budget_violation_is_visible_without_infinite_json():
    records = [{'id': 'bad', 'result': {'accepted': True,
                'hardware': {'luts': 10, 'ffs': 5, 'dsps': 1, 'brams': 0}}}]
    report = candidate_report(records, SearchConfig(objective='balanced'), None,
                              {'luts': 100, 'ffs': 50, 'dsps': 0, 'brams': 0})
    assert report['best'] is None and report['pareto'] == ['bad']
    assert report['candidates'][0]['balanced_score']['peak_utilization'] is None
    assert report['candidates'][0]['balanced_score']['unscorable_resources'] == ['dsps']
    json.dumps(report, allow_nan=False)


def test_balanced_search_audits_selected_design_and_pins_budgets_on_resume(tmp_path):
    problem, seen = harness()
    problem.resource_limits = {'luts': 100, 'ffs': 20, 'dsps': 0, 'brams': 0}
    provider = Designs([[80, 60]])
    options = {'seed': Proposal('COST=100', 1),
               'config': SearchConfig(workers=1, rounds=2, objective='balanced')}
    result = search(problem, tmp_path, provider, **options)
    assert result['selected'] == 'worker-01/round-02'
    assert result['balanced_score']['peak_utilization'] == 0.6
    assert result['improvement']['before'] == 1
    assert result['improvement']['after'] == 0.6
    before = list(seen)
    assert search(problem, tmp_path, provider, resume=True, **options) == result
    assert seen == before
    problem.resource_limits['luts'] = 120
    with pytest.raises(ValueError, match='matching'):
        search(problem, tmp_path, provider, resume=True, **options)


def test_search_protects_baseline_and_keeps_over_cap_alternative(tmp_path):
    problem, seen = harness()
    evaluate = problem.evaluate

    def resources(proposal, folder, audit):
        result = evaluate(proposal, folder, audit)
        if result['hardware']['luts'] == 70:
            result['hardware']['dsps'] = 64
        return result

    problem.evaluate = resources
    result = search(problem, tmp_path, Designs([[80, 70, 90]]), seed=Proposal('COST=100', 1),
                    config=SearchConfig(workers=1, rounds=3))
    assert result['selected'] == 'worker-01/round-01'
    assert result['resource_caps'] == {'ffs': 10, 'dsps': 0, 'brams': 0}
    assert [cost for _, cost, audit in seen if audit] == [80]
    assert {row['id'] for row in result['pareto']} == {'worker-01/round-01', 'worker-01/round-02'}
    assert result['alternatives_verification_scope'] == 'development_only'
    # Cap violations feed repair; verified-but-over-cap source is still available.
    context = json.loads((tmp_path/'worker-01/round-03/parent.json').read_text())
    assert context['parent'] == 'worker-01/round-02'
    prompt = (tmp_path/'worker-01/round-03/prompt.txt').read_text()
    assert 'BASE CAP VIOLATIONS:\n{"dsps": {"measured": 64, "maximum": 0}}' in prompt


def test_explicit_caps_override_only_one_baseline_resource_and_allow_zero():
    baseline = {'hardware': {'luts': 100, 'ffs': 10, 'dsps': 2, 'bram18_equivalents': 4}}
    assert resource_caps(SearchConfig(max_dsps=8), baseline) == {'ffs': 10, 'dsps': 8, 'brams': 4}
    assert resource_caps(SearchConfig(max_dsps=0, allow_resource_tradeoffs=True), baseline) == {'dsps': 0}
    assert resource_caps(SearchConfig(max_brams=0), None) == {'brams': 0}
    result = {'accepted': True, **baseline}
    assert not eligible(result, 'luts', {'brams': 3})  # Wi-Fi BRAM18 equivalents.
    assert not eligible({**result, 'accepted': False}, 'luts', {'dsps': 1000})


def test_cap_rejects_baseline_but_allows_repair_and_is_pinned_on_resume(tmp_path):
    problem, _ = harness()
    provider = Designs([[80, 60]])
    options = {'seed': Proposal('COST=100', 1),
               'config': SearchConfig(workers=1, rounds=2, max_luts=70)}
    result = search(problem, tmp_path, provider, **options)
    assert result['selected'] == 'worker-01/round-02'
    board = json.loads((tmp_path/'leaderboard.json').read_text())
    assert board['ranking'] == ['worker-01/round-02']
    assert search(problem, tmp_path, provider, resume=True, **options) == result
    for config in (SearchConfig(workers=1, rounds=2, max_luts=80),
                   SearchConfig(workers=1, rounds=2, max_luts=70, allow_resource_tradeoffs=True)):
        with pytest.raises(ValueError, match='matching'):
            search(problem, tmp_path, provider, seed=options['seed'], config=config, resume=True)


def test_audit_rechecks_search_caps_and_never_selects_another_candidate(tmp_path):
    problem, seen = harness()
    evaluate = problem.evaluate

    def more_dsps(proposal, folder, audit):
        result = evaluate(proposal, folder, audit)
        if audit:
            result['hardware']['dsps'] = 1
        return result

    problem.evaluate = more_dsps
    provider = Designs([[80]])
    options = {'seed': Proposal('COST=100', 1), 'config': SearchConfig(workers=1, rounds=1)}
    result = search(problem, tmp_path, provider, **options)
    assert result['status'] == 'audit_failed'
    assert result['audit']['accepted']  # The evaluator passed; the search cap did not.
    assert result['audit_constraint_violations'] == {'dsps': {'measured': 1, 'maximum': 0}}
    before = list(seen)
    assert search(problem, tmp_path, provider, resume=True, **options) == result
    assert before == seen and provider.calls == [(1, 1)]


def test_cap_with_no_seed_can_leave_no_selectable_candidate(tmp_path):
    problem, seen = harness()
    result = search(problem, tmp_path, Designs([]),
                    config=SearchConfig(workers=1, rounds=1, max_luts=20))
    assert result['status'] == 'no_passing_candidate'
    assert not any(audit for _, _, audit in seen)
    board = json.loads((tmp_path/'leaderboard.json').read_text())
    assert not board['baseline_resource_protection']
    assert board['pareto'] == ['worker-01/round-01'] and board['feasible_pareto'] == []


def test_pareto_drops_dominated_duplicates_and_includes_latency_when_requested():
    def row(name, luts, dsps, latency):
        return {'id': name, 'result': {'accepted': True, 'latency': latency,
                                     'hardware': {'luts': luts, 'ffs': 0, 'dsps': dsps, 'brams': 0}}}
    records = [row('baseline', 10, 0, 2), row('duplicate', 10, 0, 2), row('smaller', 5, 1, 2),
               row('dominated', 11, 1, 3), row('faster', 12, 0, 1)]
    assert [r['id'] for r in pareto_front(records, 'luts')] == ['smaller', 'baseline']
    assert {r['id'] for r in pareto_front(records, 'latency')} == {'faster', 'smaller', 'baseline'}


def test_exploration_branches_from_lower_dsp_frontier_member(tmp_path):
    from fpga_lab.search import _Search
    problem, _ = harness()
    engine = _Search(problem, tmp_path, SearchConfig(rounds=1, allow_resource_tradeoffs=True),
                     Designs([[0], [60]]), lambda _: None)
    records = [('baseline', 100, 0), ('low-luts', 70, 64), ('low-dsps', 80, 0)]
    for identifier, luts, dsps in records:
        folder = tmp_path/identifier
        folder.mkdir()
        (folder/'proposal.json').write_text(json.dumps({'vhdl': f'COST={luts}', 'latency': 1}))
        record = {'id': identifier, 'result': {'accepted': True,
                  'hardware': {'luts': luts, 'ffs': 10, 'dsps': dsps, 'brams': 0}}}
        engine.publish(record)
    engine.worker(1)
    parent = json.loads((tmp_path/'worker-02/round-01/parent.json').read_text())
    assert parent['focus'] == 'dsps'
    assert parent['parent'] == 'low-dsps'
    assert parent['best']['id'] == 'low-luts'


@pytest.mark.parametrize('wifi', [False, True])
def test_cli_passes_search_resource_policy(monkeypatch, tmp_path, capsys, wifi):
    from fpga_lab.cli import main
    import fpga_lab.search as module
    import fpga_lab.wifi_search as wifi_module
    called = []

    def run(problem, out, **kwargs):
        called.append(kwargs['config'])
        return {'accepted': True}

    monkeypatch.setattr(module, 'run_search', run)
    monkeypatch.setattr(module, 'translation_problem', lambda _: None)
    monkeypatch.setattr(wifi_module, 'receiver_problem', lambda *_: None)
    # wifi.run sets these defaults; keep them local to this test.
    monkeypatch.setenv('FPGA_LAB_MEMORY', '1536m')
    monkeypatch.setenv('FPGA_LAB_IMAGE', 'xls-e2e-tools:local')
    seed = tmp_path/'seed.json'
    seed.write_text(json.dumps({'vhdl': 'COST=100', 'latency': 1}))
    args = ['wifi', 'search', '--candidate', str(seed)] if wifi else ['search', 'spec.json']
    assert main([*args, '--out', str(tmp_path/'run'), '--max-dsps', '71', '--max-brams', '0',
                 '--allow-resource-tradeoffs']) == 0
    assert called[0].max_dsps == 71 and called[0].max_brams == 0
    assert called[0].allow_resource_tradeoffs
    assert called[0].objective == ('balanced' if wifi else 'luts')
    assert 'resource_changes' in capsys.readouterr().out


def test_wifi_rejects_search_only_caps_on_other_actions():
    from fpga_lab.cli import main
    with pytest.raises(SystemExit) as exc:
        main(['wifi', 'fetch', '--max-dsps', '71'])
    assert exc.value.code == 2


@pytest.mark.hardware
@pytest.mark.skipif(os.getenv('FPGA_LAB_TEST_HARDWARE') != '1', reason='Requires the Podman tools image')
def test_real_search_selects_smaller_pipeline_and_resumes_without_rebuilding(tmp_path):
    from fpga_lab.search import translation_problem

    ports = '''library ieee; use ieee.std_logic_1164.all;
entity dut is port(clk,rst,in_valid:in std_logic;
input_data:in std_logic_vector(7 downto 0);out_valid:out std_logic;
output_data:out std_logic_vector(7 downto 0));end;
'''
    two_stage = ports + '''architecture rtl of dut is
signal data1:std_logic_vector(7 downto 0);signal valid1:std_logic;
begin process(clk) begin if rising_edge(clk) then
if rst='1' then valid1<='0';out_valid<='0';data1<=(others=>'0');output_data<=(others=>'0');
else data1<=input_data;valid1<=in_valid;output_data<=data1;out_valid<=valid1;
end if;end if;end process;end;
'''
    one_stage = ports + '''architecture rtl of dut is begin
process(clk) begin if rising_edge(clk) then
if rst='1' then out_valid<='0';output_data<=(others=>'0');
else out_valid<=in_valid;output_data<=input_data;
end if;end if;end process;end;
'''
    source = tmp_path/'input'
    source.mkdir()
    (source/'identity.py').write_text('def identity(x):\n    return x\n')
    contract = {'name': 'identity', 'input_bits': 8, 'output_bits': 8,
                'max_latency': 4, 'initiation_interval': 1, 'description': 'Bit-exact unsigned identity',
                'source': 'identity.py', 'vectors': 'dev.json', 'audit_vectors': 'audit.json'}
    (source/'spec.json').write_text(json.dumps(contract))
    for name, values in [('dev', range(128)), ('audit', range(128, 256))]:
        (source/f'{name}.json').write_text(json.dumps([{'input': n, 'output': n} for n in values]))

    class RecordedDesign:
        model = 'recorded-test-design'
        calls = 0

        def request(self, prompt, folder, schema):
            self.calls += 1
            response = {'edits': [{'old': two_stage, 'new': one_stage}], 'latency': 1,
                        'notes': 'Recorded integration-test design, not a live model response'}
            (folder/'response.json').write_text(json.dumps(response))
            return response

    provider = RecordedDesign()
    options = {'config': SearchConfig(workers=1, rounds=1, objective='balanced',
                                     max_luts=128, max_ffs=9, max_dsps=0, max_brams=0),
               'seed': Proposal(two_stage, 2), 'provider': provider}
    out = tmp_path/'search'
    result = run_search(translation_problem(source/'spec.json'), out, **options)
    assert result['accepted'] and result['audit']['post_synthesis']['pass']
    assert result['development']['latency'] == 1
    assert result['improvement']['reduction'] > 0
    assert result['audit']['hardware']['ffs'] <= 9
    board = json.loads((out/'leaderboard.json').read_text())
    baseline = next(row for row in board['candidates'] if row['id'] == 'baseline')
    assert baseline['verified'] and not baseline['accepted']
    assert baseline['constraint_violations']['ffs']['maximum'] == 9
    timestamp = (out/'final/obj_dir/gate_sim').stat().st_mtime_ns
    resumed = run_search(translation_problem(source/'spec.json'), out, resume=True, **options)
    assert resumed == result and provider.calls == 1
    assert (out/'final/obj_dir/gate_sim').stat().st_mtime_ns == timestamp
