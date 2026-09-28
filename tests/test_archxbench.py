"""ArchXBench loop: oracle isolation, forbidden constructs and repair behaviour."""
import json

from fpga_lab.archxbench import Problem, problems, solve, static_check


def make_problem(root):
    folder = root / 'level-4' / 'toy'
    folder.mkdir(parents=True)
    (folder / 'problem-description.txt').write_text('Toy adder.')
    (folder / 'design-specs.txt').write_text('module toy(input a, output b);')
    (folder / 'tb.v').write_text('SECRET_TESTBENCH golden=1234')
    (folder / 'Makefile').write_text('all:\n')
    return Problem(folder)


class Provider:
    model = 'test-provider'

    def __init__(self):
        self.prompts = []

    def request(self, prompt, folder, schema):
        self.prompts.append(prompt)
        folder.mkdir(parents=True, exist_ok=True)
        good = len(self.prompts) >= 2
        return {'verilog': f'module toy(input a, output b); assign b = {"a" if good else "1\'b0"}; endmodule',
                'notes': ''}


class Bench:
    def official(self, problem, code, folder):
        passed = 'assign b = a' in code
        return {'passed': passed, 'returncode': 0 if passed else 2, 'timeout': False,
                'log': '[PASS]' if passed else '[FAIL] out[0]: dut=0 golden=1'}

    def synth(self, code, folder):
        return {'ok': True, 'timeout': False, 'cells': 1, 'log': ''}


def test_static_check_rejects_scorer_tampering():
    assert static_check('initial $fopen("outputs/dut_output.json","w");')
    assert static_check('assign x = tb_lowpass_fir.golden[3];')
    assert static_check('always @(posedge clk) $display("[PASS]");')
    assert static_check('always #5 q = d;')
    clean = ('module m #(parameter W=8)(input clk, input [W-1:0] a, output [W-1:0] b);\n'
             '  // $display in a comment is fine\n  assign b = $signed(a) >>> $clog2(W);\nendmodule')
    assert static_check(clean) == []


def test_repair_loop_feeds_back_tool_output_but_never_the_testbench(tmp_path):
    problem = make_problem(tmp_path / 'bench')
    provider = Provider()
    summary = solve(problem, Bench(), provider, tmp_path / 'out', rounds=4, progress=lambda *a, **k: None)
    assert summary['accepted'] and summary['round_passed'] == 2 and not summary['round1_pass']
    assert len(provider.prompts) == 2
    assert 'dut=0 golden=1' in provider.prompts[1] and "assign b = 1'b0" in provider.prompts[1]
    assert all('SECRET_TESTBENCH' not in p for p in provider.prompts)
    assert (tmp_path / 'out' / 'level-4/toy/final.v').read_text().count('assign b = a') == 1
    saved = json.loads((tmp_path / 'out' / 'level-4/toy/summary.json').read_text())
    assert saved['rounds_used'] == 2


def test_problem_discovery_filters(tmp_path):
    make_problem(tmp_path)
    assert [p.name for p in problems(tmp_path)] == ['level-4/toy']
    assert problems(tmp_path, ['level-6']) == []
