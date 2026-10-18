#!/usr/bin/env python3
"""Replay an exported LibreLane netlist using the independent gyro oracle."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fpga_lab.gyro_hardware import prepare, replay_sky130_netlist
from fpga_lab.problem import digest, write_json
from fpga_lab.toolchain import Toolchain


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('physical_run', type=Path, help='Directory containing inputs.json and runs/sky130')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--pdk-root', type=Path, default=ROOT / '.tools/pdks')
    parser.add_argument('--netlist', type=Path,
                        help='Explicit routed netlist; packaging still requires an exact final-netlist hash match')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    report = {'accepted': False, 'status': 'running', 'scope': 'zero-delay functional replay; STA is separate'}
    write_json(args.out / 'summary.json', report)
    try:
        inputs = json.loads((args.physical_run / 'inputs.json').read_text())
        manifest = prepare(args.out)
        if inputs['rtl_sha256'] != manifest['rtl_sha256']:
            raise ValueError('Physical run RTL differs from the current oracle regression source')
        candidates = ([args.netlist] if args.netlist else
                      sorted((args.physical_run / 'runs/sky130/final/nl').glob('*.v')))
        if len(candidates) != 1:
            raise ValueError('Expected exactly one final unpowered Verilog netlist')
        netlist = candidates[0]
        library = args.pdk_root / 'sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib'
        report.update(netlist_source=str(netlist.resolve()),
                      rtl_sha256=manifest['rtl_sha256'], netlist_sha256=digest(netlist.read_bytes()),
                      library_sha256=digest(library.read_bytes()))
        report['replay'] = replay_sky130_netlist(args.out, netlist, library, Toolchain.configured())
        report['accepted'] = report['replay']['pass']
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        report['error'] = str(exc)
    report['status'] = 'passed' if report['accepted'] else 'failed'
    write_json(args.out / 'summary.json', report)
    print(json.dumps(report, indent=2))
    return 0 if report['accepted'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
