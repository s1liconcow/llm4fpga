"""Budgeted, resumable design search with a frozen, independent final audit."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from fractions import Fraction
import fcntl
import json
import os
from pathlib import Path
import subprocess
import threading
from typing import Callable

from .problem import ROOT, digest, write_json
from .providers import CodexProvider
from .vhdl import Proposal

STRATEGIES = (
    'Reduce state and storage cost. Inspect redundant registers, resettable arrays, '
    'RAM inference, muxes, and widths justified by actual ranges.',
    'Reduce arithmetic cost. Explore resource sharing, constant arithmetic, and '
    'narrower intermediates while preserving required accuracy and throughput.',
    'Explore scheduling and pipelines. Share work only within the allowed initiation '
    'interval; keep buffers bounded and preserve sustained input processing.',
    'Explore an alternative mathematical implementation, such as lookup tables or '
    'piecewise approximations, only where the numerical contract permits it.',
)
RESOURCE_OBJECTIVES = ('luts', 'ffs', 'dsps', 'brams')
PATCH_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'edits': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'properties': {'old': {'type': 'string'}, 'new': {'type': 'string'}},
            'required': ['old', 'new']}},
        'latency': {'type': 'integer'}, 'notes': {'type': 'string'}},
    'required': ['edits', 'latency', 'notes'],
}


@dataclass(frozen=True)
class SearchConfig:
    workers: int = 2
    rounds: int = 3
    tool_jobs: int = 1
    objective: str = 'luts'
    max_luts: int | None = None
    max_ffs: int | None = None
    max_dsps: int | None = None
    max_brams: int | None = None
    allow_resource_tradeoffs: bool = False

    def __post_init__(self):
        for name, limit in [('workers', 32), ('rounds', 50), ('tool_jobs', 8)]:
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= limit:
                raise ValueError(f'--{name.replace("_", "-")} must be in 1..{limit}')
        if self.objective not in (*RESOURCE_OBJECTIVES, 'latency', 'balanced'):
            raise ValueError('Unknown search objective')
        for key in RESOURCE_OBJECTIVES:
            value = getattr(self, f'max_{key}')
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f'--max-{key} must be a nonnegative integer')
        if type(self.allow_resource_tradeoffs) is not bool:
            raise ValueError('allow_resource_tradeoffs must be a boolean')

    @classmethod
    def from_args(cls, args):
        return cls(**{name: getattr(args, name) for name in cls.__dataclass_fields__})


def add_resource_arguments(parser):
    for key in RESOURCE_OBJECTIVES:
        parser.add_argument(f'--max-{key}', type=int,
                            help=f'Hard search cap for {key}; never relaxes the verification contract')
    parser.add_argument('--allow-resource-tradeoffs', action='store_true',
                        help='Allow other resources to grow above the baseline, within explicit and contract caps')


@dataclass
class SearchProblem:
    prompt: str
    identity: dict
    evaluate: Callable[[Proposal, Path, bool], dict]
    objectives: tuple[str, ...] = (*RESOURCE_OBJECTIVES, 'latency', 'balanced')
    artifacts: tuple[str, ...] = ()
    resource_limits: dict[str, int] = field(default_factory=dict)


def read_seed(path: Path | None) -> Proposal | None:
    if path is None:
        return None
    if path.suffix.lower() in ('.vhd', '.vhdl'):
        return Proposal(path.read_text(), 1, 'Starting receiver implementation')
    return Proposal.parse(path.read_text())


def apply_edits(parent: Proposal, response: dict) -> Proposal:
    """Apply exact replacements; never execute model-supplied patch commands."""
    if not isinstance(response.get('edits'), list):
        raise ValueError('Expected an edits array')
    source = parent.vhdl
    for edit in response['edits']:
        if not isinstance(edit, dict) or not isinstance(edit.get('old'), str) or not isinstance(edit.get('new'), str):
            raise ValueError('Each edit requires old and new strings')
        old = edit['old']
        if not old or source.count(old) != 1:
            raise ValueError('Each old string must match exactly once; include enough surrounding context')
        source = source.replace(old, edit['new'], 1)
    return Proposal.parse(json.dumps({'vhdl': source, 'latency': response.get('latency'),
                                     'notes': response.get('notes', '')}))


def metrics(result: dict) -> dict:
    hardware = result.get('hardware', {})
    return {'luts': hardware.get('luts'), 'ffs': hardware.get('ffs'),
            'dsps': hardware.get('dsps'),
            'brams': hardware.get('bram18_equivalents', hardware.get('brams')),
            'latency': result.get('latency')}


def resource_caps(config: SearchConfig, baseline: dict | None) -> dict:
    """An explicit cap authorizes growth; otherwise protect other baseline resources."""
    measured = metrics(baseline or {})
    caps = {}
    for key in RESOURCE_OBJECTIVES:
        explicit = getattr(config, f'max_{key}')
        if explicit is not None:
            caps[key] = explicit
        elif not config.allow_resource_tradeoffs and config.objective != 'balanced' and key != config.objective:
            value = measured[key]
            if type(value) is int and value >= 0:
                caps[key] = value
    return caps


def balanced_budgets(config: SearchConfig, limits: dict | None) -> dict:
    """Normalize to available budgets, never to unrelated primitive counts."""
    if config.objective != 'balanced':
        return {}
    budgets = {}
    for key in RESOURCE_OBJECTIVES:
        values = [value for value in ((limits or {}).get(key), getattr(config, f'max_{key}'))
                  if value is not None]
        if not values or any(type(value) is not int or value < 0 for value in values):
            raise ValueError(f'balanced requires a resource budget for {key}; set --max-{key} or a contract/target limit')
        budgets[key] = min(values)
    return budgets


def utilization(result: dict, budgets: dict) -> dict:
    measured = metrics(result)
    return {key: (Fraction(measured[key], limit) if limit else
                  Fraction(0) if measured[key] == 0 else None)
            if type(measured[key]) is int and measured[key] >= 0 else None
            for key, limit in budgets.items()}


def balanced_score(result: dict, budgets: dict) -> dict | None:
    if not budgets:
        return None
    fractions = utilization(result, budgets)
    valid = all(value is not None for value in fractions.values())
    peak = max(fractions.values()) if valid else None
    return {'budgets': budgets,
            'utilization': {key: float(value) if value is not None else None for key, value in fractions.items()},
            'peak_utilization': float(peak) if peak is not None else None,
            'total_utilization': float(sum(fractions.values())) if valid else None,
            'limiting_resources': [key for key, value in fractions.items() if value == peak],
            'unscorable_resources': [key for key, value in fractions.items() if value is None]}


def constraint_violations(result: dict, caps: dict) -> dict:
    measured = metrics(result)
    return {key: {'measured': measured[key], 'maximum': limit} for key, limit in caps.items()
            if type(measured[key]) is not int or not 0 <= measured[key] <= limit}


def eligible(result: dict, objective: str, caps: dict | None = None) -> bool:
    measured = metrics(result)
    required = set(RESOURCE_OBJECTIVES) | ({'latency'} if objective == 'latency' else set())
    return (result.get('accepted') is True and all(
        type(measured[key]) is int and measured[key] >= 0 for key in required)
        and not constraint_violations(result, caps or {}))


def rank(record: dict, objective: str, budgets: dict | None = None) -> tuple:
    measured = metrics(record['result'])
    if objective == 'balanced':
        fractions = utilization(record['result'], budgets or {})
        if not fractions or any(value is None for value in fractions.values()):
            raise ValueError('Cannot rank balanced candidate without valid resource usage and budgets')
        return (max(fractions.values()), sum(fractions.values()),
                *[fractions[key] for key in RESOURCE_OBJECTIVES], record['id'])
    keys = [objective, *(key for key in RESOURCE_OBJECTIVES if key != objective)]
    # Stable ties keep the baseline instead of claiming an identical design improved.
    return (*[measured[key] for key in keys], record['id'])


def pareto_front(records: list[dict], objective: str) -> list[dict]:
    """Keep resource tradeoffs; equal measurements retain one stable representative."""
    keys = list(dict.fromkeys([*(['latency'] if objective == 'latency' else []), *RESOURCE_OBJECTIVES]))
    frontier = []
    for row in sorted(records, key=lambda r: (*[metrics(r['result'])[key] for key in keys], r['id'])):
        values = metrics(row['result'])
        if not any(all(metrics(other['result'])[key] <= values[key] for key in keys)
                   for other in frontier):
            frontier.append(row)
    return frontier


def resource_changes(result: dict, baseline: dict | None) -> dict:
    before, after = metrics(baseline or {}), metrics(result)
    changes = {}
    for key in RESOURCE_OBJECTIVES:
        old, new = before[key], after[key]
        valid = type(old) is int and old >= 0 and type(new) is int and new >= 0
        changes[key] = {'before': old, 'after': new, 'delta': new-old if valid else None,
                        'percent': 100*(new-old)/old if valid and old else None}
    return changes


def candidate_report(records: list[dict], config: SearchConfig, baseline: dict | None,
                     resource_limits: dict | None = None) -> dict:
    """Development-only comparisons, also usable with previously measured candidates."""
    budgets = balanced_budgets(config, resource_limits)
    caps = {**resource_caps(config, baseline), **budgets}
    verified = [r for r in records if eligible(r.get('result', {}), config.objective)]
    passing = sorted((r for r in verified if eligible(r['result'], config.objective, caps)),
                     key=lambda r: rank(r, config.objective, budgets))
    rows = []
    for row in sorted(records, key=lambda r: r['id']):
        result = row.get('result', {})
        rows.append({'id': row['id'], 'parent': row.get('parent'),
                     'vhdl_sha256': result.get('vhdl_sha256'),
                     'verified': eligible(result, config.objective),
                     'accepted': eligible(result, config.objective, caps),
                     'resources': metrics(result), 'resource_changes': resource_changes(result, baseline),
                     'balanced_score': balanced_score(result, budgets),
                     'constraint_violations': constraint_violations(result, caps),
                     'error': result.get('error', row.get('provider_error'))})
    return {'objective': config.objective, 'resource_caps': caps,
            'balanced_budgets': budgets,
            'baseline_resource_protection': baseline is not None and not config.allow_resource_tradeoffs and config.objective != 'balanced',
            'verification_scope': 'development_only',
            'best': passing[0]['id'] if passing else None,
            'ranking': [r['id'] for r in passing],
            'pareto': [r['id'] for r in pareto_front(verified, config.objective)],
            'feasible_pareto': [r['id'] for r in pareto_front(passing, config.objective)],
            'candidates': rows}


def feedback(result: dict) -> dict:
    """Keep complete diagnostics without copying cell inventories or packet traces."""
    compact = {key: result[key] for key in ('accepted', 'error', 'latency') if key in result}
    compact['resources'] = metrics(result)
    if 'numeric' in result:
        compact['numeric'] = result['numeric']
    for stage in ('rtl', 'mapped', 'post_synthesis'):
        if stage in result:
            value = result[stage]
            compact[stage] = {key: value[key] for key in ('pass', 'cases_run', 'cases_total', 'failures') if key in value}
            compact[stage]['failed_cases'] = [
                {key: row[key] for key in ('name', 'pass', 'failures', 'throughput', 'error', 'debug_tail') if key in row}
                for row in value.get('results', []) if not row.get('pass')][:8]
    return compact


def environment_identity() -> dict:
    """A mutable image tag alone is not sufficient identity for cached evaluations."""
    from .toolchain import Toolchain
    tools = Toolchain.configured()
    image_id = subprocess.run([tools.runtime, 'image', 'inspect', '--format', '{{.Id}}', tools.image],
                              check=True, capture_output=True, text=True, timeout=30).stdout.strip()
    if not image_id:
        raise ValueError('Could not identify the hardware tools image')
    sources = [*sorted((ROOT/'fpga_lab').glob('*.py')), *sorted((ROOT/'fpga_lab/hdl').glob('*.v'))]
    return {'runtime': tools.runtime, 'image': tools.image, 'image_id': image_id,
            'platform': tools.platform,
            'harness': {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in sources},
            'settings': {key: os.getenv(key) for key in (
                'FPGA_LAB_MEMORY', 'FPGA_LAB_WIFI_FORMAL', 'FPGA_LAB_WIFI_JOBS')}}


def translation_problem(spec_path: Path) -> SearchProblem:
    from .contracts import TranslationSpec, load_vectors
    from .toolchain import Toolchain
    from .translate import INSTRUCTIONS
    from .vhdl import evaluate
    spec_path = spec_path.resolve()
    spec = TranslationSpec.load(spec_path)
    source_path = spec_path.parent/spec.source
    dev_path, audit_path = spec_path.parent/spec.vectors, spec_path.parent/spec.audit_vectors
    if source_path.suffix.lower() not in ('.py', '.m'):
        raise ValueError('Source must be Python or MATLAB')
    if dev_path.resolve() == audit_path.resolve():
        raise ValueError('Development and audit vectors must be separate files')
    source = source_path.read_text()
    development = load_vectors(dev_path, spec)
    audit_hash = digest(audit_path.read_bytes())
    tools = Toolchain.configured()

    def check(proposal, folder, audit):
        if audit and digest(audit_path.read_bytes()) != audit_hash:
            raise ValueError('Audit vectors changed during search')
        vectors = load_vectors(audit_path, spec) if audit else development
        result = evaluate(proposal, spec, vectors, folder, tools)
        if audit:
            (folder/source_path.name).write_text(source)
        return result

    return SearchProblem(
        prompt=INSTRUCTIONS+'\nCONTRACT:\n'+json.dumps(spec.public_contract(), indent=2)
            +'\nSOURCE:\n'+source+'\nDEVELOPMENT EXAMPLES:\n'
            +json.dumps([v.as_dict() for v in development[:8]]),
        identity={'kind': 'translation', 'contract': spec.public_contract(),
                  'source_sha256': digest(source.encode()),
                  'development_sha256': digest(dev_path.read_bytes()), 'audit_sha256': audit_hash},
        evaluate=check, artifacts=('mapped.v', 'netlist.json', 'obj_dir/gate_sim'),
        resource_limits={key: getattr(spec, f'max_{key}') for key in RESOURCE_OBJECTIVES
                         if getattr(spec, f'max_{key}') is not None})


def run_search(problem: SearchProblem, out: Path, *, config: SearchConfig | None = None,
               seed: Proposal | None = None, model: str | None = None, resume: bool = False,
               provider=None, environment: dict | None = None, progress=None) -> dict:
    config = config or SearchConfig()
    if config.objective not in problem.objectives:
        raise ValueError(f'{config.objective} is not measured for this search problem')
    balanced_budgets(config, problem.resource_limits)  # Fail before generation or hardware work.
    if progress is None:
        progress = lambda message: print(message, flush=True)
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    with (out/'.search.lock').open('a') as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Another search owns this output directory') from exc
        provider = provider or CodexProvider(model, timeout=3600)
        manifest = {'version': 2, 'problem': problem.identity,
                    'resource_limits': problem.resource_limits,
                    'prompt_sha256': digest(problem.prompt.encode()), 'config': asdict(config),
                    'seed_sha256': digest(json.dumps(asdict(seed), sort_keys=True).encode()) if seed else None,
                    'model': provider.model,
                    'environment': environment if environment is not None else environment_identity()}
        manifest_path = out/'manifest.json'
        if resume:
            if not manifest_path.exists() or json.loads(manifest_path.read_text()) != manifest:
                raise ValueError('Resume requires matching source, contract, fixtures, seed, model, search settings and tools')
        else:
            if any(p.name != '.search.lock' for p in out.iterdir()):
                raise ValueError('Choose a fresh output directory or --resume')
            write_json(manifest_path, manifest)
            (out/'problem.txt').write_text(problem.prompt)
        engine = _Search(problem, out, config, provider, progress)
        return engine.run(seed)


class _Search:
    def __init__(self, problem, out, config, provider, progress):
        self.problem, self.out, self.config = problem, out, config
        self.provider, self.progress = provider, progress
        self.records: dict[str, dict] = {}
        self.baseline = None
        self.budgets = balanced_budgets(config, problem.resource_limits)
        self.caps = {**resource_caps(config, None), **self.budgets}
        self.lock = threading.Lock()
        self.tools = threading.Semaphore(config.tool_jobs)
        self.stop = threading.Event()

    def load(self, folder: Path) -> dict | None:
        path = folder/'record.json'
        if not path.exists():
            return None
        record = json.loads(path.read_text())
        for name, expected in record['files'].items():
            artifact = folder/name
            if not artifact.is_file() or digest(artifact.read_bytes()) != expected:
                raise ValueError(f'Cached search artifact changed: {artifact}')
        return record

    def save(self, folder, record):
        names = ['proposal.json', 'candidate.vhd', 'result.json', 'prompt.txt', 'parent.json', 'response.json']
        if record.get('result', {}).get('accepted'):
            for name in self.problem.artifacts:
                if not (folder/name).is_file():
                    raise ValueError(f'Accepted evaluation is missing {name}')
            names += list(self.problem.artifacts)
        record['files'] = {name: digest((folder/name).read_bytes()) for name in names if (folder/name).is_file()}
        write_json(folder/'record.json', record)
        return record

    def passing(self):
        return sorted((r for r in self.records.values()
                       if eligible(r.get('result', {}), self.config.objective, self.caps)),
                      key=lambda row: rank(row, self.config.objective, self.budgets))

    def report(self):
        return candidate_report(list(self.records.values()), self.config,
                                self.baseline['result'] if self.baseline else None,
                                self.problem.resource_limits)

    def publish(self, record):
        with self.lock:
            self.records[record['id']] = record
            write_json(self.out/'leaderboard.json', self.report())
        result = record.get('result', {})
        self.progress(f'{record["id"]}: accepted={eligible(result, self.config.objective, self.caps)}; '
                      f'resources={json.dumps(metrics(result))}; '
                      f'cap_violations={json.dumps(constraint_violations(result, self.caps))}; '
                      f'{str(result.get("error", record.get("provider_error", "")))[:300]}')

    def evaluate(self, folder, proposal, *, audit=False):
        write_json(folder/'proposal.json', asdict(proposal))
        (folder/'candidate.vhd').write_text(proposal.vhdl)
        with self.tools:
            self.progress(f'{folder.relative_to(self.out)}: evaluating {"audit" if audit else "development"}...')
            try:
                result = self.problem.evaluate(proposal, folder, audit)
            except Exception as exc:
                result = {'accepted': False, 'error': str(exc)}
        source_hash = digest(proposal.vhdl.encode())
        if result.get('vhdl_sha256', source_hash) != source_hash:
            raise ValueError('Evaluator returned a result for different VHDL')
        result['vhdl_sha256'] = source_hash
        write_json(folder/'result.json', result)
        return result

    def propose(self, folder, parent, prompt):
        saved = folder/'proposal.json'
        if saved.exists():
            return Proposal.parse(saved.read_text())
        response_path = folder/'response.json'
        if response_path.exists():
            response = json.loads(response_path.read_text())
        elif parent:
            response = self.provider.request(prompt, folder, PATCH_SCHEMA)
        else:
            return self.provider.propose(prompt, folder)
        return apply_edits(parent, response) if parent else Proposal.parse(json.dumps(response))

    def worker(self, index):
        previous = None
        for iteration in range(self.config.rounds):
            if self.stop.is_set():
                return
            identifier = f'worker-{index+1:02d}/round-{iteration+1:02d}'
            folder = self.out/identifier
            folder.mkdir(parents=True, exist_ok=True)
            record = self.load(folder)
            if record:
                self.publish(record)
                if 'result' in record:
                    previous = record
                continue
            parent_path = folder/'parent.json'
            if parent_path.exists():
                context = json.loads(parent_path.read_text())
                parent = Proposal.parse(json.dumps(context['proposal'])) if context['proposal'] else None
                prompt = (folder/'prompt.txt').read_text()
            else:
                # Keep one worker on the requested objective; other workers explore
                # resource-efficient branches rather than all copying one incumbent.
                alternatives = [key for key in ('dsps', 'brams', 'ffs', 'luts') if key != self.config.objective]
                focus = self.config.objective if index == 0 else alternatives[(index+iteration-1) % len(alternatives)]
                with self.lock:
                    passing = self.passing()
                    best = passing[0] if passing else None
                    frontier = pareto_front(passing, self.config.objective)
                    branch = min(frontier, key=lambda r: rank(r, focus, self.budgets)) if frontier else None
                # Keep a failed local candidate available for repair. Once it passes,
                # branch from a resource-specific member of the feasible frontier.
                base = previous if previous and not eligible(previous['result'], self.config.objective, self.caps) else branch
                if base is None:
                    base = previous or self.baseline
                parent = Proposal.parse((self.out/base['id']/'proposal.json').read_text()) if base else None
                context = {'parent': base['id'] if base else None,
                           'proposal': asdict(parent) if parent else None,
                           'focus': focus, 'resource_caps': self.caps,
                           'balanced_budgets': self.budgets,
                           'frontier': [{'id': r['id'], 'resources': metrics(r['result'])} for r in frontier],
                           'best': {'id': best['id'], 'feedback': feedback(best['result'])} if best else None}
                prompt = (self.problem.prompt+f'\nSEARCH worker {index+1}, round {iteration+1}.\n'
                          +STRATEGIES[index % len(STRATEGIES)]
                          +f'\nThis branch explores lower {focus}; final selection minimizes measured {self.config.objective}. '
                          'Obey EVERY contract constraint and the additional hard search caps below. '
                          'An explicit cap overrides baseline protection for that resource only, never the contract. '
                          'Continue improving passing designs. Resource counts are measured after Xilinx mapping; '
                          'do not claim physical timing or power results. No tools or testbench changes.\n'
                          +('Balanced minimizes the highest usage/budget ratio across LUTs, FFs, DSPs and BRAMs, '
                            'then the sum of those ratios. Trade resources within caps to reduce the bottleneck.\n'
                            if self.budgets else '')
                          +'HARD SEARCH RESOURCE CAPS:\n'+json.dumps(self.caps)
                          +'\nFEASIBLE DEVELOPMENT PARETO FRONTIER:\n'+json.dumps(context['frontier'])
                          +'\nCURRENT BEST DEVELOPMENT RESULT:\n'+json.dumps(context['best']))
                if parent:
                    prompt += ('\nCOMPLETE BASE CANDIDATE:\n'+parent.vhdl
                               +'\nBASE DEVELOPMENT FEEDBACK:\n'+json.dumps(feedback(base['result']))
                               +'\nBASE CAP VIOLATIONS:\n'+json.dumps(constraint_violations(base['result'], self.caps))
                               +'\nFor this search iteration return the EDIT schema instead of full VHDL: '
                               '{edits:[{old,new}],latency,notes}. Each nonempty old string must match exactly '
                               'once in the current source, applied in order. Include exact whitespace and enough '
                               'context. Keep unchanged source intact. Empty edits means no change. '
                               'Explain the expected resource saving and behavioral justification in notes.')
                (folder/'prompt.txt').write_text(prompt)
                write_json(parent_path, context)
            self.progress(f'{identifier}: requesting design from Codex...')
            try:
                proposal = self.propose(folder, parent, prompt)
            except Exception as exc:
                record = self.save(folder, {'id': identifier, 'parent': context['parent'], 'provider_error': str(exc)})
                self.publish(record)
                continue
            result = self.evaluate(folder, proposal)
            record = self.save(folder, {'id': identifier, 'parent': context['parent'], 'result': result})
            self.publish(record)
            previous = record

    def finish(self, selection):
        identifier = selection['id']
        baseline = self.load(self.out/'baseline')
        self.caps = {**resource_caps(self.config, baseline['result'] if baseline else None), **self.budgets}
        if selection['resource_caps'] != self.caps:
            raise ValueError('Frozen resource caps changed')
        selected = self.load(self.out/identifier)
        if not selected or not eligible(selected.get('result', {}), self.config.objective, self.caps):
            raise ValueError('Frozen selection is missing or no longer eligible')
        proposal = Proposal.parse((self.out/identifier/'proposal.json').read_text())
        if digest(proposal.vhdl.encode()) != selection['vhdl_sha256']:
            raise ValueError('Frozen selection changed')
        final = self.out/'final'
        final.mkdir(exist_ok=True)
        audit_record = self.load(final)
        if audit_record is None:
            audit = self.evaluate(final, proposal, audit=True)
            audit_record = self.save(final, {'id': 'final', 'selected': identifier, 'result': audit})
        if audit_record['selected'] != identifier or audit_record['result']['vhdl_sha256'] != selection['vhdl_sha256']:
            raise ValueError('Final audit does not match the frozen selection')
        audit = audit_record['result']
        accepted = eligible(audit, self.config.objective, self.caps)
        baseline_score = balanced_score(baseline['result'], self.budgets) if baseline else None
        selected_score = balanced_score(selected['result'], self.budgets)
        before = (baseline_score['peak_utilization'] if baseline_score else
                  metrics(baseline['result'])[self.config.objective] if baseline else None)
        after = selected_score['peak_utilization'] if selected_score else metrics(selected['result'])[self.config.objective]
        summary = {'accepted': accepted, 'status': 'passed' if accepted else 'audit_failed',
                   'objective': self.config.objective, 'selected': identifier,
                   'development': selected['result'], 'audit': audit,
                   'baseline': baseline['result'] if baseline else None,
                   'resource_caps': self.caps,
                   'balanced_score': selected_score, 'baseline_balanced_score': baseline_score,
                   'resource_changes': resource_changes(selected['result'], baseline['result'] if baseline else None),
                   'audit_constraint_violations': constraint_violations(audit, self.caps),
                   'pareto': selection['pareto'],
                   'alternatives_verification_scope': 'development_only',
                   'improvement': {'before': before, 'after': after,
                       'reduction': before-after if before is not None else None,
                       'percent': 100*(before-after)/before if before else None},
                   'physical_timing_measured': False}
        write_json(self.out/'summary.json', summary)
        return summary

    def run(self, seed):
        # A selection file is a terminal boundary: resume can finish/reuse its audit,
        # but cannot launch more workers or use audit failures to choose another design.
        if (self.out/'selection.json').exists():
            return self.finish(json.loads((self.out/'selection.json').read_text()))
        write_json(self.out/'summary.json', {'accepted': False, 'status': 'searching'})
        pool = None
        try:
            if seed:
                folder = self.out/'baseline'
                folder.mkdir(exist_ok=True)
                record = self.load(folder)
                if record is None:
                    result = self.evaluate(folder, seed)
                    record = self.save(folder, {'id': 'baseline', 'parent': None, 'result': result})
                # A baseline outside the budgets still supplies useful source and
                # feedback; it is never eligible for selection until it passes.
                self.baseline = record
                self.caps = {**resource_caps(self.config, record['result']), **self.budgets}
                self.publish(record)
            pool = ThreadPoolExecutor(max_workers=self.config.workers)
            futures = [pool.submit(self.worker, i) for i in range(self.config.workers)]
            for future in as_completed(futures):
                future.result()
            pool.shutdown()
            passing = self.passing()
            report = self.report()
            frontier_rows = [row for row in report['candidates'] if row['id'] in report['pareto']]
            if not passing:
                summary = {'accepted': False, 'status': 'no_passing_candidate', 'audit': None,
                           'objective': self.config.objective, 'candidates': len(self.records),
                           'resource_caps': self.caps, 'pareto': frontier_rows,
                           'alternatives_verification_scope': 'development_only'}
                write_json(self.out/'summary.json', summary)
                return summary
            best = passing[0]
            selection = {'id': best['id'], 'vhdl_sha256': best['result']['vhdl_sha256'],
                         'objective': self.config.objective, 'resources': metrics(best['result']),
                         'resource_caps': self.caps,
                         'pareto': frontier_rows}
            write_json(self.out/'selection.json', selection)
            return self.finish(selection)
        except BaseException:
            self.stop.set()
            if pool:
                pool.shutdown(wait=True, cancel_futures=True)
            write_json(self.out/'summary.json', {'accepted': False, 'status': 'interrupted'})
            raise
