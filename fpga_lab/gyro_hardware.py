"""Gyro Verilog and ASIC extension of the existing isolated tool harness."""
from __future__ import annotations

from dataclasses import asdict
import json
import math
from pathlib import Path
import random
import re
import shutil
import subprocess

from .gyro import Config, Gyro, ONE, analytic_demodulation, demo_config, quantize
from .problem import ROOT, digest, write_json
from .toolchain import Toolchain


def fixtures(seed: int = 8301) -> list[tuple[str, Config, list[tuple[int, int]]]]:
    rng = random.Random(seed)
    wave = [(quantize(.5 * math.cos(2 * math.pi * n / 16)),
             quantize(.125 * math.cos(2 * math.pi * n / 16) + .0625 * math.sin(2 * math.pi * n / 16)))
            for n in range(512)]
    result = [('carrier_iq', demo_config(), wave)]
    for name, lengths, large in [
        ('full_128_taps', [128] * 7, False),
        ('mixed_lengths', [1, 2, 3, 17, 63, 127, 128], False),
        ('saturation', [1, 3, 1, 7, 2, 1, 5], True),
    ]:
        coeff = [[rng.randint(-131072, 131071) if large else rng.randint(-8192, 8192)
                  for _ in range(length)] for length in lengths]
        config = Config(coefficients=coeff, gains=[rng.randint(-131072, 131071) for _ in range(8)],
                        kp=98304, ki=-131072 if large else 512, reference=131071,
                        i_min=-262144, i_max=196608, u_min=-98304, u_max=114688)
        samples = [(rng.randint(-131072, 131071), rng.randint(-131072, 131071)) for _ in range(300)]
        samples[:8] = [(0, 0), (-131072, 131071), (131071, -131072), (-1, 1), (1, -1),
                       (-131072, -131072), (131071, 131071), (0, 0)]
        result.append((name, config, samples))
    # Full-scale FIR accumulation, negative minimum abs, and 18/22/24-bit clamps.
    config = Config(coefficients=[[131071] * 128 for _ in range(7)],
                    gains=[131071] * 8, kp=-131072, ki=131071, reference=2097151,
                    i_min=-8388608, i_max=8388607, u_min=-2097152, u_max=2097151)
    result.append(('accumulator_extremes', config, [(131071, 131071)] * 140 + [(-131072, -131072)] * 140))
    return result


def prepare(out: Path, seed: int = 8301) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / 'summary.json', {'accepted': False, 'status': 'preparing', 'tapeout_ready': False})
    for name in ('gyro_core.v', 'gyro_tb.v'):
        shutil.copy2(ROOT / 'fpga_lab/hdl' / name, out / name)
    stimulus, expected, cases = [], [], []
    for name, config, samples in fixtures(seed):
        stimulus.append('0 0 0 0')
        stimulus.extend(f'1 {address} {value} 0' for address, value in config.writes())
        stimulus.append('5 0 0 0')
        model = Gyro(config)
        start = len(expected)
        for a, b in samples:
            stimulus.append(f'2 {a} {b} 0')
            expected.append(list(model.step(a, b)))
        cases.append({'name': name, 'start': start, 'samples': len(samples),
                      'latency_cycles': 68 + sum(map(len, config.coefficients)), 'config': asdict(config)})
        # Abort a real in-flight sample and ensure both control and history reset.
        stimulus.append('4 12345 -54321 0')
        stimulus.append('2 131071 -131072 0')
        expected.append([0] * 5)
        cases.append({'name': name + '_reset', 'start': len(expected)-1, 'samples': 1, 'latency_cycles': 75})
    (out / 'stimulus.txt').write_text('\n'.join(stimulus) + '\n')
    write_json(out / 'expected.json', expected)
    manifest = {'seed': seed, 'cases': cases, 'outputs': len(expected),
                'rtl_sha256': digest((out / 'gyro_core.v').read_bytes()),
                'testbench_sha256': digest((out / 'gyro_tb.v').read_bytes()),
                'oracle_sha256': digest((ROOT / 'fpga_lab/gyro.py').read_bytes()),
                'stimulus_sha256': digest((out / 'stimulus.txt').read_bytes()),
                'expected_sha256': digest((out / 'expected.json').read_bytes()),
                'analytic': analytic_demodulation()}
    write_json(out / 'manifest.json', manifest)
    write_json(out / 'summary.json', {'accepted': False, 'status': 'prepared', 'tapeout_ready': False,
                                    'manifest': str(out / 'manifest.json')})
    return manifest


def scoreboard(out: Path, manifest: dict) -> dict:
    expected = json.loads((out / 'expected.json').read_text())
    rows = [[int(x) for x in line.split()] for line in (out / 'trace.txt').read_text().splitlines()]
    failures = []
    if any(len(row) != 7 for row in rows):
        return {'pass': False, 'outputs': len(rows), 'failures': [{'error': 'Malformed trace row'}], 'cases': []}
    if any(b[0] <= a[0] for a, b in zip(rows, rows[1:])):
        failures.append({'error': 'Non-monotonic output cycle'})
    if len(rows) != len(expected):
        failures.append({'error': 'Output count mismatch', 'expected': len(expected), 'actual': len(rows)})
    for i, (row, want) in enumerate(zip(rows, expected)):
        if len(row) != 7 or row[2:] != want:
            failures.append({'sample': i, 'expected': want, 'actual': row})
    measured = []
    for case in manifest['cases']:
        subset = rows[case['start']:case['start'] + case['samples']]
        latencies = [r[1] for r in subset if len(r) == 7]
        # The monitor observes pre-edge valid; output stalls add up to 19 cycles.
        low = case['latency_cycles'] + 1
        if any(not low <= latency <= low + 19 for latency in latencies):
            failures.append({'case': case['name'], 'error': 'Latency outside schedule/stall bound'})
        measured.append({'name': case['name'], 'samples': len(subset),
                         'min_cycles': min(latencies, default=0), 'max_cycles': max(latencies, default=0)})
    return {'pass': not failures, 'outputs': len(rows), 'failures': failures[:10], 'cases': measured}


def sky130_synthesis(out: Path, pdk_root: Path, tools: Toolchain) -> dict:
    """Technology-map with an installed PDK even without the full PnR image."""
    library = pdk_root / 'sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib'
    shutil.copy2(library, out / 'sky130.lib')
    script = ('read_verilog gyro_core.v\nsynth -top gyro_core\n'
              'dfflibmap -liberty sky130.lib\nabc -liberty sky130.lib\nclean\n'
              'read_liberty -lib sky130.lib\ncheck -assert\n'
              'tee -o sky130-stat.json stat -json -liberty sky130.lib\n'
              'write_verilog -noattr -noexpr sky130-mapped.v\n')
    (out / 'sky130-synth.ys').write_text(script)
    tools.run(['yosys', '-s', 'sky130-synth.ys'], out, timeout=600, log='sky130-synthesis.log')
    checked = replay_sky130_netlist(out, out / 'sky130-mapped.v', library, tools)
    return {'pass': True, 'library_sha256': digest(library.read_bytes()),
            'mapped_sha256': digest((out / 'sky130-mapped.v').read_bytes()),
            'functional_gate_replay': checked,
            'simulation_models': 'Yosys Liberty-derived Boolean/FF models; zero-delay, two-state',
            'statistics': json.loads((out / 'sky130-stat.json').read_text()),
            'scope': 'Sky130 TT cell mapping only; no physical timing, DRC, LVS, or power signoff'}


def empty_physical_cells(source: str) -> list[str]:
    """Identify portless PDK models containing only optional internal supplies."""
    source = re.sub(r'/\*.*?\*/|//[^\n]*', '', source, flags=re.S)
    good, bad = set(), set()
    for name, body in re.findall(
        r'\bmodule\s+(sky130_fd_sc_hd__(?:tapvpwrvgnd|fill|decap)_\d+)\s*'
        r'\(\s*\)\s*;(.*?)\bendmodule\b', source, flags=re.S):
        supply_only = re.fullmatch(
            r'(?:\s*(?:supply1\s+(?:VPWR|VPB)|supply0\s+(?:VGND|VNB))\s*;)*\s*', body)
        (good if supply_only else bad).add(name)
    # Every portless variant must be empty; never select one empty variant while
    # overlooking functional behavior in another variant of the same cell.
    return sorted(good - bad)


def replay_sky130_netlist(out: Path, netlist: Path, library: Path, tools: Toolchain) -> dict:
    """Replay a mapped or final physical netlist against prepared oracle vectors."""
    shutil.copy2(netlist, out / 'replay-netlist.v')
    if library.resolve() != (out / 'sky130.lib').resolve():
        shutil.copy2(library, out / 'sky130.lib')
    # Physical-only tap/fill/decap cells have no logic pins and are absent from
    # Liberty. Accept only portless models with no behavior beyond supplies. Do
    # not synthesize stubs for any unknown or unsupported functional cell.
    pdk_verilog = library.parent.parent / 'verilog/sky130_fd_sc_hd.v'
    empty = empty_physical_cells(pdk_verilog.read_text())
    if 'sky130_fd_sc_hd__tapvpwrvgnd_1' not in empty:
        raise ValueError('PDK physical-only cell models are missing or have changed')
    (out / 'physical-only.v').write_text(
        '// Physical-only models from SkyWater PDK. Copyright 2020 The SkyWater PDK Authors.\n'
        '// SPDX-License-Identifier: Apache-2.0\n' +
        '\n'.join(f'module {name} (); endmodule' for name in sorted(set(empty))) + '\n')
    write_json(out / 'physical-models.json', {'source_sha256': digest(pdk_verilog.read_bytes()),
                                             'empty_physical_only_cells': sorted(set(empty)),
                                             'normalization': 'Bind every cell, then remove only verified empty physical-only instances'})
    # Verilator does not implement the PDK's UDP tables. Derive actual Boolean
    # and flip-flop models from the same Liberty instead; never black-box UDPs.
    # Some unused physical/clock cells have no Liberty Boolean function. Omit
    # them, then require every instantiated cell to resolve in hierarchy/check.
    tools.run(['yosys', '-p', 'read_liberty -ignore_miss_func sky130.lib; '
               'read_verilog -nooverwrite -noblackbox physical-only.v; '
               'read_verilog replay-netlist.v; hierarchy -check -top gyro_core; '
               'check -assert; delete ' + ' '.join('t:' + name for name in sorted(set(empty))) + '; '
               'check -assert; write_verilog -noattr sky130-functional.v'],
              out, timeout=180, log='sky130-models.log')
    tools.run(['verilator', '--binary', '--timing', '-j', '1', '-Wno-fatal',
               '--output-split', '10000', '--output-split-cfuncs', '500',
               '--top-module', 'gyro_tb', '--Mdir', 'sky130_obj', '-o', 'sky130_sim',
               'sky130-functional.v', 'gyro_tb.v'],
              out, timeout=600, log='sky130-build.log')
    (out / 'trace.txt').unlink(missing_ok=True)
    tools.run(['./sky130_obj/sky130_sim'], out, timeout=180, log='sky130-simulation.log')
    checked = scoreboard(out, json.loads((out / 'manifest.json').read_text()))
    shutil.copy2(out / 'trace.txt', out / 'sky130_trace.txt')
    if not checked['pass']:
        raise ValueError(f'Sky130 gate replay failed: {checked}')
    return checked


def run(out: Path, *, synthesize: bool = False, seed: int = 8301, sky130_pdk: Path | None = None,
        tools: Toolchain | None = None) -> dict:
    tools = tools or Toolchain.configured()
    out.mkdir(parents=True, exist_ok=True)
    # Invalidate previous acceptance before overwriting any run artifacts.
    write_json(out / 'summary.json', {'accepted': False, 'status': 'preparing', 'tapeout_ready': False})
    manifest = prepare(out, seed)
    report = {'accepted': False, 'analytic': manifest['analytic'], 'tapeout_ready': False,
              'physical_signoff': 'not_run', 'manifest': str(out / 'manifest.json'), 'status': 'running'}
    write_json(out / 'summary.json', report)
    try:
        tools.run(['sh', '-ec', 'iverilog -g2005 -Wall -s gyro_tb -o sim gyro_core.v gyro_tb.v\nvvp sim'],
                  out, timeout=180, log='simulation.log')
        report['rtl'] = scoreboard(out, manifest)
        shutil.copy2(out / 'trace.txt', out / 'rtl_trace.txt')
        if synthesize and report['rtl']['pass']:
            script = ('read_verilog gyro_core.v\nhierarchy -check -top gyro_core\n'
                      'synth -top gyro_core\ncheck -assert\n'
                      'tee -o synthesis.json stat -json\nwrite_verilog -noattr mapped.v\n')
            (out / 'synth.ys').write_text(script)
            tools.run(['yosys', '-s', 'synth.ys'], out, timeout=1200, log='synthesis.log')
            report['generic_synthesis'] = json.loads((out / 'synthesis.json').read_text())
            # The register-backed memories create ~86k generic gates. Compiled
            # simulation avoids the event simulator's cost on this large netlist.
            tools.run(['verilator', '--binary', '--timing', '-j', '1', '-Wno-fatal',
                       '--output-split', '10000', '--output-split-cfuncs', '500',
                       '--top-module', 'gyro_tb', '--Mdir', 'mapped_obj', '-o', 'mapped_sim',
                       'mapped.v', 'gyro_tb.v'], out, timeout=600, log='mapped_build.log')
            tools.run(['./mapped_obj/mapped_sim'], out, timeout=180, log='mapped_simulation.log')
            report['mapped'] = scoreboard(out, manifest)
            report['mapped_sha256'] = digest((out / 'mapped.v').read_bytes())
            shutil.copy2(out / 'trace.txt', out / 'mapped_trace.txt')
        if sky130_pdk and report['rtl']['pass']:
            report['sky130_synthesis'] = sky130_synthesis(out, sky130_pdk, tools)
        report['accepted'] = (report['analytic']['pass'] and report['rtl']['pass'] and
                              (not synthesize or report['mapped']['pass']))
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        report['error'] = str(exc)
    report['status'] = 'passed' if report['accepted'] else 'failed'
    write_json(out / 'summary.json', report)
    return report


def add_parser(sub) -> None:
    parser = sub.add_parser('gyro', help='Verify the programmable seven-FIR MEMS gyro Verilog core')
    parser.add_argument('--out', type=Path, default=Path('runs/gyro'))
    parser.add_argument('--seed', type=int, default=8301)
    parser.add_argument('--synthesize', action='store_true', help='Also synthesize and replay generic mapped gates')
    parser.add_argument('--prepare-only', action='store_true', help='Write deterministic vectors without running tools')
    parser.add_argument('--sky130-pdk', type=Path, help='Also map to the installed Sky130 HD standard-cell library')
    parser.add_argument('--closed-loop', action='store_true', help='Run a live Python resonator / Verilator feedback test')
    parser.add_argument('--kp', type=float, default=1.75, help='Proportional gain for --closed-loop')
    parser.add_argument('--ki', type=float, default=1 / 1024, help='Integral gain per sample for --closed-loop')


def command(args) -> int:
    if args.closed_loop:
        if args.prepare_only or args.synthesize or args.sky130_pdk:
            raise ValueError('--closed-loop runs the live RTL experiment; do not combine with other modes')
        from .gyro_loop import run as run_loop
        report = run_loop(args.out, kp=args.kp, ki=args.ki)
    else:
        report = prepare(args.out, args.seed) if args.prepare_only else run(
            args.out, synthesize=args.synthesize, seed=args.seed, sky130_pdk=args.sky130_pdk)
    print(json.dumps(report, indent=2))
    return 0 if args.prepare_only or report['accepted'] else 2
