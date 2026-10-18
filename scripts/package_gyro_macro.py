#!/usr/bin/env python3
"""Package a physically checked macro and its matching functional replay."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def package(physical: Path, replay: Path, output: Path) -> dict:
    signoff = json.loads((physical / 'signoff.json').read_text())
    simulation = json.loads((replay / 'summary.json').read_text())
    inputs = json.loads((physical / 'inputs.json').read_text())
    if not signoff.get('macro_signoff_pass') or signoff.get('flow_exit_code') != 0:
        raise ValueError('Physical macro checks have not passed')
    if not simulation.get('accepted') or not simulation.get('replay', {}).get('pass'):
        raise ValueError('Final-netlist functional replay has not passed')
    final = physical / 'runs/sky130/final'
    netlists = list((final / 'nl').glob('*.v'))
    if len(netlists) != 1 or sha256(netlists[0]) != simulation['netlist_sha256']:
        raise ValueError('Replayed netlist differs from the final physical netlist')
    if not signoff.get('artifact_hashes'):
        raise ValueError('Physical artifact hashes are missing')
    for name, evidence in signoff['artifact_hashes'].items():
        path = physical / 'runs/sky130' / name
        if path.stat().st_size != evidence['bytes'] or sha256(path) != evidence['sha256']:
            raise ValueError(f'Physical artifact changed after validation: {name}')
    rtl_hash = sha256(physical / 'gyro_core.v')
    if rtl_hash != inputs['rtl_sha256'] or rtl_hash != simulation['rtl_sha256']:
        raise ValueError('Physical and replay RTL identities differ')
    for name, digest in inputs['flow_files_sha256'].items():
        if sha256(physical / name) != digest:
            raise ValueError(f'Saved physical flow input changed: {name}')
    replay_inputs = json.loads((replay / 'manifest.json').read_text())
    for path, key in ((ROOT / 'fpga_lab/gyro.py', 'oracle_sha256'),
                      (replay / 'gyro_tb.v', 'testbench_sha256'),
                      (replay / 'stimulus.txt', 'stimulus_sha256'),
                      (replay / 'expected.json', 'expected_sha256')):
        if sha256(path) != replay_inputs[key]:
            raise ValueError(f'Replay source changed after validation: {path.name}')
    files = {}
    suffixes = {'.gds', '.lef', '.v', '.sdc', '.spef', '.sdf', '.spice', '.lib', '.json'}
    for path in final.rglob('*'):
        if path.is_file() and path.suffix in suffixes:
            files['physical/' + str(path.relative_to(final))] = path
    for suffix in ('.gds', '.lef', '.v', '.spef', '.sdc'):
        matching = [p for p in files.values() if p.suffix == suffix]
        if not matching or any(p.stat().st_size == 0 for p in matching):
            raise ValueError(f'Missing or empty final {suffix} view')
    for name in ('signoff.json', 'inputs.json', 'toolchain.json', 'config.json',
                 'checkpoint-provenance.json', 'last-resume.json', 'gyro_core.v', 'gyro_flow.py',
                 'electrical_repair.tcl', 'extracted_repair.tcl', 'targeted_sizing.tcl'):
        path = physical / name
        if path.exists():
            files['provenance/' + name] = path
    for name in ('summary.json', 'manifest.json', 'physical-models.json',
                 'expected.json', 'stimulus.txt', 'gyro_tb.v', 'sky130_trace.txt',
                 'sky130-functional.v', 'sky130-models.log', 'sky130-build.log',
                 'sky130-simulation.log'):
        files['replay/' + name] = replay / name
    files['reference/gyro.py'] = ROOT / 'fpga_lab/gyro.py'
    files['docs/gyro-design.md'] = ROOT / 'docs/gyro-design.md'
    files['LICENSE.librelane'] = ROOT / 'examples/gyro/sky130/LICENSE.librelane'
    files['LICENSE.skywater'] = ROOT / 'examples/gyro/sky130/LICENSE.librelane'
    files['NOTICE.md'] = ROOT / 'examples/gyro/sky130/NOTICE.md'
    files['examples/gyro/validation.json'] = ROOT / 'examples/gyro/validation.json'
    for name in ('gyro-routing.png', 'routing.lyp'):
        if (physical / name).exists():
            files['preview/' + name] = physical / name
    manifest = {'scope': 'digital macro only; not a chip-level fabrication submission',
                'tapeout_ready': False, 'rtl_sha256': rtl_hash,
                'files': {name: {'sha256': sha256(path), 'bytes': path.stat().st_size}
                          for name, path in sorted(files.items())}}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + '.tmp')
    readme = ('Gyro controller digital macro\n\n'
              'See docs/gyro-design.md for the interface, numerical contract, assumptions,\n'
              'reproduction commands, and integration requirements. Physical checks and\n'
              'the matching zero-delay functional replay are included. The manifest records\n'
              'every payload hash. Fanout-target advisories remain in the physical metrics.\n\n'
              'This is not a fabrication submission. A shuttle wrapper, pads/ESD, analog\n'
              'interfaces, final system constraints, calibrated coefficients, and sensor\n'
              'validation are still required. No fabrication has been ordered.\n')
    readme_bytes = readme.encode()
    manifest['files']['README.txt'] = {
        'sha256': hashlib.sha256(readme_bytes).hexdigest(), 'bytes': len(readme_bytes)}
    try:
        with tarfile.open(temporary, 'w:gz', dereference=True) as archive:
            for name, path in sorted(files.items()):
                archive.add(path, arcname=name, recursive=False)
            for name, value in [('manifest.json', json.dumps(manifest, indent=2) + '\n'),
                                ('README.txt', readme)]:
                payload = value.encode()
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return {'path': str(output), 'bytes': output.stat().st_size, 'sha256': sha256(output),
            'payload_files': len(manifest['files']), 'tapeout_ready': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('physical_run', type=Path)
    parser.add_argument('replay_run', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.physical_run, args.replay_run, args.out), indent=2))
