"""Held-out audit for ArchXBench designs selected by the repair loop.

The official testbenches use a few fixed vectors (for example the four SP 800-38A AES
blocks under one key, or an identity matrix for GEMM), and the loop's feedback came from
those same vectors. Each audit reruns the unmodified official testbench and scorer on
fresh, seeded stimuli whose goldens come from the benchmark's own reference scripts (or,
where none exists, an independent reference here). Nothing here reaches the model.
"""
from __future__ import annotations

import json
import re
import shutil
import struct
from pathlib import Path

import numpy as np

from .archxbench import Bench, Problem
from .problem import write_json

SEEDS = (101, 202, 303)


def _dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=1))


def fir(work: Path, rng: np.random.Generator) -> None:
    taps = len(json.loads((work / 'filters/filter.json').read_text())['filter'])
    half = rng.integers(-12000, 12001, taps // 2 + 1)
    coeffs = np.concatenate([half, half[-2::-1]])  # symmetric, as the spec promises
    samples = rng.integers(-50000, 50001, 1000)
    samples[100:160] = 50000   # sustained full-scale steps stress accumulator width
    samples[400:460] = -50000
    _dump(work / 'filters/filter.json', {'filter': coeffs.tolist()})
    _dump(work / 'inputs/input.json', {'input': [0] + samples[1:].tolist()})


def fft16(work: Path, rng: np.random.Generator) -> None:
    _dump(work / 'inputs/stimulus_fft.json', rng.integers(-1500, 1501, 16).tolist())
    _dump(work / 'inputs/stimulus_ifft.json', {'real': rng.integers(-1500, 1501, 16).tolist(),
                                               'imag': rng.integers(-1500, 1501, 16).tolist()})


def conv1d(work: Path, rng: np.random.Generator) -> None:
    _dump(work / 'inputs/stimuli.json', rng.integers(0, 256, 512).tolist())


def conv2d(work: Path, rng: np.random.Generator) -> None:
    image = rng.integers(0, 256, 4096)
    image[:64] = 255  # saturated first row plus noise
    _dump(work / 'inputs/stimuli.json', image.tolist())


def dct(work: Path, rng: np.random.Generator) -> None:
    _dump(work / 'inputs/stimuli.json', rng.integers(-4000, 4001, 24).tolist())


def gemm(work: Path, rng: np.random.Generator) -> None:
    mats = {k: rng.integers(-2**30, 2**30, (4, 4)) for k in ('A_first', 'B_first', 'A_second', 'B_second')}
    _dump(work / 'inputs/stimuli.json', {k: v.flatten().tolist() for k, v in mats.items()})
    # The tb prints the unsigned [63:0] result ports, so compare two's-complement bit patterns.
    gold = {f'C_{n}': [int(v) % 2**64 for v in (mats[f'A_{n}'].astype(object) @ mats[f'B_{n}'].astype(object)).flatten()]
            for n in ('first', 'second')}
    _dump(work / 'outputs/golden_output.json', gold)


def harris(work: Path, rng: np.random.Generator) -> None:
    seed = int(rng.integers(1 << 30))
    script = work / 'scripts/generate_golden.py'
    override = f'''
def _audit_image(seed):
    r = np.random.default_rng(seed)
    img = (r.integers(0, 40, (IMG_HEIGHT, IMG_WIDTH))).astype(np.uint8)
    for _ in range(6):
        y, x = r.integers(4, IMG_HEIGHT - 40), r.integers(4, IMG_WIDTH - 40)
        h, w = r.integers(8, 36, 2)
        img[y:y + h, x:x + w] = r.integers(120, 256)
    return img.flatten().tolist()
def generate_cross():
    return _audit_image({seed})
def generate_checkerboard():
    return _audit_image({seed + 1})
'''
    text = script.read_text()
    script.write_text(text.replace('TESTCASES = {', override + '\nTESTCASES = {', 1))


def aes(work: Path, rng: np.random.Generator) -> None:
    script = work / 'scripts/generate_golden.py'
    blocks = [bytes(rng.integers(0, 256, 16, dtype=np.uint8)).hex() for _ in range(64)]
    keys = [bytes(rng.integers(0, 256, 16, dtype=np.uint8)).hex() for _ in range(64)]
    listing = lambda xs: '[\n' + ''.join(f'    bytes.fromhex("{x}"),\n' for x in xs) + ']'
    text = script.read_text()
    for name, values in (('PLAINTEXTS', blocks), ('KEYS', keys)):
        text, count = re.subn(rf'^{name} = \[.*?^\]', f'{name} = {listing(values)}', text, flags=re.S | re.M)
        assert count == 1, name
    script.write_text(text)


def conv3d(work: Path, rng: np.random.Generator) -> None:
    # The official tb hard-codes one kernel on the input port; change it in tb and reference alike.
    kernel = rng.integers(0, 2, (5, 5, 5))
    bus = sum(int(v) << (8 * i) for i, v in enumerate(kernel.flatten()))
    tb = work / 'tb.v'
    tb.write_text(re.sub(r"1000'h[0-9a-fA-F]+", f"1000'h{bus:0250x}", tb.read_text(), count=1))
    seed = int(rng.integers(1 << 30))
    script = work / 'scripts/generate_golden.py'
    override = f"""
KERNEL = np.array({kernel.tolist()}, dtype=np.int64)
def make_impulse():
    vol = np.zeros((D, H, W), dtype=np.int64)
    r = np.random.default_rng({seed})
    for _ in range(4):
        vol[r.integers(D), r.integers(H), r.integers(W)] = 255
    return vol
def make_ramp():
    return np.random.default_rng({seed + 1}).integers(0, 256, size=(D, H, W))
def make_random():
    return np.random.default_rng({seed + 2}).integers(0, 256, size=(D, H, W))
"""
    script.write_text(script.read_text().replace('TESTCASES = {', override + '\nTESTCASES = {', 1))


def fft64(work: Path, rng: np.random.Generator) -> None:
    script = work / 'scripts/generate_input.py'
    values = (rng.uniform(-0.95, 0.95, 64)).tolist()
    text, count = re.subn(r'^x_float = \[.*?^\]', f'x_float = {values}', script.read_text(), flags=re.S | re.M)
    assert count == 1
    script.write_text(text)


def fp_fir(work: Path, rng: np.random.Generator) -> None:
    to_hex = lambda v: f'0x{struct.unpack(">I", struct.pack(">f", float(v)))[0]:08x}'
    taps = len(json.loads((work / 'filters/filter.json').read_text())['filter'])
    half = rng.normal(0, 0.08, taps // 2 + 1)
    coeffs = np.concatenate([half, half[-2::-1]]).astype(np.float32)
    samples = rng.uniform(-2.0, 2.0, 1000).astype(np.float32)
    _dump(work / 'filters/filter.json', {'filter': [to_hex(v) for v in coeffs]})
    _dump(work / 'inputs/input.json', {'input': [to_hex(v) for v in samples]})


def multich_conv2d(work: Path, rng: np.random.Generator) -> None:
    image = rng.integers(0, 256, (3, 128, 128))
    kernel = rng.integers(-3, 4, (8, 3, 3, 3))
    bias = rng.integers(-256, 257, 8)
    script = work / 'scripts/generate_stimuli.py'
    script.write_text(script.read_text() + f"""
# Held-out audit overrides.
with open(os.path.join(root, "inputs", "input.json"), "w") as f:
    json.dump({{"image": {image.tolist()}}}, f)
with open(os.path.join(root, "inputs", "kernel.json"), "w") as f:
    json.dump({{"names": channel_names, "kernel": {kernel.tolist()}, "bias": {bias.tolist()}}}, f)
""")


def quantized_matmul(work: Path, rng: np.random.Generator) -> None:
    # The spec defines no saturation, so keep every quantized value inside INT8.
    while True:
        seed = int(rng.integers(1 << 31))
        old = np.random.get_state()
        np.random.seed(seed)
        a, b = np.random.uniform(-1, 1, (8, 64)), np.random.uniform(-1, 1, (64, 8))
        sa, sb = (round(10 ** np.random.uniform(-3, -0.3) * 32768) for _ in range(2))
        za, zb = np.random.randint(-16, 17), np.random.randint(-16, 17)
        np.random.set_state(old)
        if (np.abs(np.round(a / (sa / 32768))).max() + abs(za) <= 127
                and np.abs(np.round(b / (sb / 32768))).max() + abs(zb) <= 127):
            break
    script = work / 'scripts/generate_golden.py'
    script.write_text(script.read_text().replace('np.random.seed(42)', f'np.random.seed({seed})', 1))


def unsharp(work: Path, rng: np.random.Generator) -> None:
    from PIL import Image
    image = rng.integers(0, 256, (256, 256)).astype(np.uint8)
    for _ in range(12):  # flat regions and hard edges exercise the clamp at 0 and 255
        y, x = rng.integers(0, 200, 2)
        image[y:y + rng.integers(8, 56), x:x + rng.integers(8, 56)] = rng.choice([0, 255])
    Image.fromarray(image, 'L').save(work / 'resources/lenna.png')


MUTATORS = {
    'level-4/low_pass_fir': fir, 'level-4/high_pass_fir': fir, 'level-4/band_pass_fir': fir,
    'level-4/fft_ifft_16pt_iterative': fft16, 'level-5/conv1d': conv1d, 'level-5/conv2d': conv2d,
    'level-5/dct_idct_8pt_pipelined': dct, 'level-5/systolic_gemm': gemm,
    'level-5/harris_corner_detection': harris, 'level-5/unsharp_mask': unsharp,
    'level-6/aes_encryption': aes, 'level-6/aes_decryption': aes, 'level-6/conv_3d': conv3d,
    'level-6/fft_streaming_64pt': fft64, 'level-6/fp_low_pass_fir': fp_fir,
    'level-6/fp_high_pass_fir': fp_fir, 'level-6/fp_band_pass_fir': fp_fir,
    'level-6/multich_conv2d': multich_conv2d, 'level-6/quantized_matmul': quantized_matmul,
}


# ---- IEEE-754 pipelines: the official tb hard-codes its vectors, so stream our own. ----

FP_TB = '''`timescale 1ns/1ps
module tb_audit;
  reg clk = 0, rst = 1, valid_in = 0, add_sub = 0;
  reg [31:0] a = 0, b = 0;
  wire [31:0] result; wire valid_out;
  reg [64:0] vec [0:NMAX-1];
  integer n, i, got, cyc, f;
  always #5 clk = ~clk;
  DUT_INST
  initial begin
    n = 0; $readmemh("vectors.hex", vec);
    f = $fopen("results.hex", "w");
    for (i = 0; i < NMAX; i = i + 1) if (vec[i] !== 65'bx) n = i + 1;
    repeat (4) @(posedge clk); rst = 0;
    // Back-to-back issue, one op per cycle, with a bubble every 7th cycle.
    for (i = 0; i < n; i = i + 1) begin
      @(negedge clk); {add_sub, a, b} = vec[i]; valid_in = 1;
      if (i % 7 == 6) begin @(negedge clk); valid_in = 0; end
    end
    @(negedge clk); valid_in = 0;
    repeat (64) @(posedge clk);
    $fclose(f); $finish;
  end
  always @(posedge clk) if (!rst && valid_out) $fwrite(f, "%h\\n", result);
endmodule
'''


def _f32(bits: int) -> np.float32:
    return np.frombuffer(struct.pack('<I', bits), dtype=np.float32)[0]


def _bits(value: np.float32) -> int:
    return struct.unpack('<I', np.float32(value).tobytes())[0]


def _fp_vectors(rng: np.random.Generator, mult: bool, count: int = 4000) -> list[tuple[int, int, int, str]]:
    """(sub, a, b, class): 'normal' vectors have normal operands and normal exact results."""
    out = []
    while len(out) < count:
        kind = 'normal' if len(out) < count * 3 // 4 else 'edge'
        if kind == 'normal':
            span = 60 if mult else 30
            ea, eb = rng.integers(127 - span, 127 + span, 2)
            if not mult and rng.random() < 0.3:
                eb = ea + rng.integers(-2, 3)  # near-cancellation paths
            a = (int(rng.integers(2)) << 31) | (int(ea) << 23) | int(rng.integers(1 << 23))
            b = (int(rng.integers(2)) << 31) | (int(eb) << 23) | int(rng.integers(1 << 23))
        else:
            specials = [0, 0x80000000, 0x7F800000, 0xFF800000, 0x7FC00000, 0x00000001,
                        0x007FFFFF, 0x00800000, 0x7F7FFFFF, 0x80400000]
            pick = lambda: specials[rng.integers(len(specials))] if rng.random() < 0.5 else int(rng.integers(1 << 32))
            a, b = pick(), pick()
        sub = 0 if mult else int(rng.integers(2))
        with np.errstate(all='ignore'):
            x, y = _f32(a), _f32(b)
            r = x * y if mult else (x - y if sub else x + y)
        if kind == 'normal' and not (np.isfinite(r) and (r == 0 or abs(r) >= np.finfo(np.float32).tiny)):
            continue
        out.append((sub, a, b, kind))
    return out


def fp_audit(bench: Bench, problem: Problem, code: str, folder: Path, seed: int) -> dict:
    mult = 'mult' in problem.root.name
    vectors = _fp_vectors(np.random.default_rng(seed), mult)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'code.v').write_text(code)
    ports = '.a(a), .b(b), .valid_in(valid_in), .result(result), .valid_out(valid_out)'
    inst = (f'fp_mult_pipeline dut(.clk(clk), .rst(rst), {ports});' if mult else
            f'fp_adder_pipeline dut(.clk(clk), .rst(rst), .add_sub(add_sub), {ports});')
    (folder / 'tb_audit.v').write_text(FP_TB.replace('NMAX', str(len(vectors))).replace('DUT_INST', inst))
    (folder / 'vectors.hex').write_text(''.join(f'{(s << 64) | (a << 32) | b:017x}\n' for s, a, b, _ in vectors))
    rc, log = bench._container('iverilog -g2012 -o audit.out code.v tb_audit.v && vvp -n audit.out',
                               folder, bench.sim_timeout, 'audit.log')
    lines = (folder / 'results.hex').read_text().split() if (folder / 'results.hex').exists() else []
    stats = {'normal': [0, 0], 'edge': [0, 0]}
    failures = []
    for (sub, a, b, kind), got in zip(vectors, lines):
        with np.errstate(all='ignore'):
            x, y = _f32(a), _f32(b)
            want = _bits(x * y if mult else (x - y if sub else x + y))
        g = int(got, 16) if re.fullmatch(r'[0-9a-f]{8}', got) else -1
        is_nan = lambda v: (v >> 23) & 0xFF == 0xFF and v & 0x7FFFFF
        ok = g == want or (is_nan(want) and g >= 0 and is_nan(g))
        stats[kind][0 if ok else 1] += 1
        if not ok and len(failures) < 12:
            failures.append({'kind': kind, 'sub': sub, 'a': f'{a:08x}', 'b': f'{b:08x}',
                             'want': f'{want:08x}', 'got': got})
    complete = rc == 0 and len(lines) == len(vectors)
    return {'passed': complete and stats['normal'][1] == 0, 'complete': complete,
            'outputs': len(lines), 'expected': len(vectors),
            'normal_pass_fail': stats['normal'], 'edge_pass_fail': stats['edge'], 'failures': failures}


def audit_problem(bench: Bench, problem: Problem, code: str, folder: Path) -> dict:
    trials = []
    for seed in SEEDS:
        tdir = folder / f'seed-{seed}'
        if tdir.exists():
            shutil.rmtree(tdir)
        if problem.root.name.startswith('fp_') and problem.root.parent.name == 'level-4':
            trials.append({'seed': seed, **fp_audit(bench, problem, code, tdir, seed)})
            continue
        mutate = MUTATORS.get(problem.name)
        if mutate is None:
            return {'problem': problem.name, 'audited': False, 'reason': 'no audit mutator'}
        # Run the official flow on a mutated copy; the pristine problem is never touched.
        staged = Problem(tdir / 'problem')
        shutil.copytree(problem.root, staged.root, ignore=shutil.ignore_patterns('.DS_Store', '*.out'))
        mutate(staged.root, np.random.default_rng(seed))
        result = bench.official(staged, code, tdir)
        trials.append({'seed': seed, 'passed': result['passed'], 'returncode': result['returncode'],
                       'tail': result['log'][-1500:]})
    summary = {'problem': problem.name, 'audited': True, 'passed': all(t['passed'] for t in trials),
               'trials': trials}
    write_json(folder / 'audit.json', summary)
    return summary


def audit_run(bench_dir: Path, run_dir: Path, *, tool_jobs: int = 3, only: list[str] | None = None,
              progress=print) -> dict:
    from concurrent.futures import ThreadPoolExecutor
    from .archxbench import IMAGE, problems, selected_round
    from .toolchain import Toolchain
    bench = Bench(Toolchain(Toolchain.configured().runtime, IMAGE), tool_jobs)
    # Audit every design that passed the official flow, including synthesis-inconclusive ones.
    chosen = []
    for problem in problems(bench_dir, only):
        # Per-round results survive an interrupted run; summary.json is written only at the end.
        selected = selected_round(run_dir / problem.name)
        if selected:
            chosen.append((problem, selected / 'code.v'))

    def one(item):
        problem, code = item
        result = audit_problem(bench, problem, code.read_text(), run_dir / problem.name / 'audit')
        progress(f'{problem.name}: audited={result["audited"]} passed={result.get("passed")}', flush=True)
        return {**result, 'code': str(code.relative_to(run_dir))}

    with ThreadPoolExecutor(tool_jobs) as pool:
        results = list(pool.map(one, chosen))
    report = {'audited': sum(r['audited'] for r in results),
              'audit_passed': sum(bool(r.get('passed')) for r in results),
              'results': [{k: v for k, v in r.items() if k != 'trials'} for r in results]}
    write_json(run_dir / 'audit.json', report)
    return report
