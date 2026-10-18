#!/usr/bin/env python3
"""Run a pinned, isolated LibreLane macro flow and collect signoff evidence.

This builds a digital macro, not a pad-ring/ADC/DAC/MEMS die. No shuttle upload
or fabrication order is performed. Missing metrics are failures, never zeroes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fpga_lab.problem import write_json

IMAGE = 'ghcr.io/librelane/librelane:3.0.16'
PDK_REVISION = '8afc8346a57fe1ab7934ba5a6056ea8b43078e71'
LEGACY_POST_ANTENNA_REPAIR_FLOW = {
    'version': 2, 'flow': 'Classic', 'substituting_steps': [
        ['OpenROAD.RepairDesignPostGRT', None],
        ['+OpenROAD.RepairAntennas', 'OpenROAD.RepairDesignPostGRT'],
    ],
}
POST_ANTENNA_REPAIR_FLOW = {
    **LEGACY_POST_ANTENNA_REPAIR_FLOW, 'substituting_steps': [
        ['OpenROAD.RepairDesignPostGRT', None],
        ['+OpenROAD.RepairAntennas', 'Gyro.ElectricalRepair'],
    ],
}
EXTRACTED_REPAIR_FLOW = {
    **POST_ANTENNA_REPAIR_FLOW,
    'substituting_steps': POST_ANTENNA_REPAIR_FLOW['substituting_steps'] + [
        ['+OpenROAD.RCX', 'Gyro.ExtractedRepair'],
    ],
}
REQUIRED_ZERO = (
    'magic__drc_error__count', 'klayout__drc_error__count',
    'design__lvs_unmatched_device__count', 'design__lvs_unmatched_net__count',
    'design__lvs_unmatched_pin__count', 'design__lvs_property_fail__count',
    'timing__setup_vio__count', 'timing__hold_vio__count',
    'design__max_slew_violation__count', 'design__max_cap_violation__count',
    'route__antenna_violation__count', 'design__critical_disconnected_pin__count',
    'design__lvs_error__count', 'route__drc_errors', 'magic__illegal_overlap__count',
    'design__xor_difference__count', 'design__power_grid_violation__count',
    'timing__unannotated_net_filtered__count',
)


def verify_snapshot(out: Path, manifest: dict) -> None:
    expected = {'gyro_core.v': manifest['rtl_sha256'], **manifest['flow_files_sha256']}
    for name, digest in expected.items():
        if hashlib.sha256((out / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f'Saved physical input changed: {name}')
    if json.loads((out / 'config.json').read_text()) != manifest['config']:
        raise ValueError('Saved physical configuration changed')


def verify_image_identity(record: Path, image_id: str) -> None:
    if json.loads(record.read_text())['image_id'] != image_id:
        raise ValueError('Container image identity differs from the saved physical run')


def copy_post_cts_checkpoint(source: Path, out: Path, manifest: dict, *, post_grt: bool = False,
                             post_rcx: bool = False) -> dict:
    """Reuse CTS only for an identical design with known later-stage tuning."""
    prior = json.loads((source / 'inputs.json').read_text())
    allowed = {'PL_RESIZER_HOLD_SLACK_MARGIN', 'GRT_RESIZER_HOLD_SLACK_MARGIN',
               'RUN_HEURISTIC_DIODE_INSERTION', 'RUN_POST_GRT_DESIGN_REPAIR',
               'RUN_POST_GRT_RESIZER_TIMING', 'GRT_DESIGN_REPAIR_MAX_SLEW_PCT',
               'GRT_DESIGN_REPAIR_MAX_CAP_PCT', 'meta'}
    for c in (prior['config'], manifest['config']):
        if c.get('meta') not in (None, LEGACY_POST_ANTENNA_REPAIR_FLOW, POST_ANTENNA_REPAIR_FLOW, EXTRACTED_REPAIR_FLOW):
            raise ValueError('Checkpoint flow changes are not a supported post-CTS substitution')
    normalized = lambda c: {k: v for k, v in c.items() if k not in allowed}
    if (prior['rtl_sha256'] != manifest['rtl_sha256'] or
        prior['pdk_revision'] != manifest['pdk_revision'] or prior['image'] != manifest['image'] or
        normalized(prior['config']) != normalized(manifest['config'])):
        raise ValueError('CTS checkpoint differs beyond the permitted post-CTS repair settings')
    state_path = source / 'runs/sky130/36-openroad-stamidpnr-1/state_out.json'
    start_step = 'OpenROAD.ResizerTimingPostCTS'
    if post_grt:
        states = list((source / 'runs/sky130').glob('*-openroad-stamidpnr-3/state_out.json'))
        if not states:
            raise ValueError('No post-global-route timing checkpoint is available')
        state_path = max(states, key=lambda p: int(p.parent.name.split('-', 1)[0]))
        start_step = 'Gyro.ElectricalRepair'
    if post_rcx:
        states = list((source / 'runs/sky130').glob('*-openroad-rcx/state_out.json'))
        states += list((source / 'runs/sky130').glob('*-gyro-extractedrepair/state_out.json'))
        if not states:
            raise ValueError('No completed extraction checkpoint is available')
        state_path = max(states, key=lambda p: int(p.parent.name.split('-', 1)[0]))
        start_step = 'Gyro.ExtractedRepair'
    state = json.loads(state_path.read_text())
    if post_rcx and (not state.get('odb') or
                    not all(state.get('spef', {}).get(k + '_*') for k in ('min', 'nom', 'max'))):
        raise ValueError('Extraction checkpoint must include ODB and all three RC views')
    hashes = {}
    def copy(value):
        if isinstance(value, dict):
            return {k: copy(v) for k, v in value.items()}
        if isinstance(value, str) and value.startswith('/work/'):
            relative = Path(value.removeprefix('/work/'))
            original = source / relative
            target = out / 'checkpoint' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
            hashes[str(relative)] = hashlib.sha256(target.read_bytes()).hexdigest()
            return '/work/checkpoint/' + str(relative)
        return value
    write_json(out / 'checkpoint-state.json', copy(state))
    provenance = {'source_inputs': prior, 'state_sha256': hashlib.sha256(state_path.read_bytes()).hexdigest(),
                  'view_sha256': hashes, 'from_step': start_step}
    write_json(out / 'checkpoint-provenance.json', provenance)
    return provenance


def collect(run: Path, exit_code: int) -> dict:
    metrics_path = run / 'final/metrics.json'
    metrics = json.loads(metrics_path.read_text()) if metrics_path.exists() else {}
    checks = {key: {'value': metrics.get(key), 'pass': metrics.get(key) == 0} for key in REQUIRED_ZERO}
    artifacts = {suffix: sorted(str(p.relative_to(run)) for p in (run / 'final').rglob('*' + suffix))
                 for suffix in ('.gds', '.lef', '.v', '.spef', '.sdc')}
    artifact_hashes = {}
    for names in artifacts.values():
        for name in names:
            path = run / name
            with path.open('rb') as stream:
                artifact_hashes[name] = {'sha256': hashlib.file_digest(stream, 'sha256').hexdigest(),
                                         'bytes': path.stat().st_size}
    passed = (exit_code == 0 and all(c['pass'] for c in checks.values()) and all(artifacts.values())
              and all(v['bytes'] > 0 for v in artifact_hashes.values()))
    return {'macro_signoff_pass': passed, 'tapeout_ready': False,
            'scope': 'digital macro; shuttle wrapper/pads, analog interface and sensor validation still required',
            'flow_exit_code': exit_code, 'checks': checks, 'artifacts': artifacts,
            'artifact_hashes': artifact_hashes,
            'advisories': {'max_fanout_target_violations': metrics.get('design__max_fanout_violation__count'),
                          'note': 'Generic fanout target is advisory; slew/capacitance limits are acceptance checks. Review loads at integration.'},
            'metrics': metrics}


def resume_point(out: Path) -> tuple[str, Path]:
    # LibreLane's existing run tag loads state but does not skip earlier steps.
    # Repeat the last attempted top-level step from its exact input state.
    steps = [p for p in (out / 'runs/sky130').iterdir()
             if p.is_dir() and p.name.split('-', 1)[0].isdigit()]
    if not steps:
        raise ValueError('No physical step is available to resume')
    last = max(steps, key=lambda p: int(p.name.split('-', 1)[0]))
    step_id = json.loads((last / 'config.json').read_text())['meta']['step']
    state = last / 'state_in.json'
    if not state.exists():
        raise ValueError('The last physical step has no saved input state')
    return step_id, state.relative_to(out)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, default=ROOT / 'runs/gyro-sky130')
    p.add_argument('--pdk-root', type=Path, default=ROOT / '.tools/pdks')
    p.add_argument('--runtime', default='podman')
    p.add_argument('--memory', default='11g', help='Bounded container RAM allowance (default: 11g)')
    p.add_argument('--sta-threads', type=int, default=1,
                   help='Timing corners in parallel; one avoids multiplying extracted-netlist memory')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--post-cts-checkpoint', type=Path,
                   help='Reuse identical RTL/placement/CTS, allowing known later-stage repair settings')
    p.add_argument('--post-grt-checkpoint', type=Path,
                   help='Reuse a routed timing checkpoint for another electrical-repair pass')
    p.add_argument('--post-rcx-checkpoint', type=Path,
                   help='Reuse completed detailed routing and extraction for electrical repair')
    p.add_argument('--to', help='Stop at a LibreLane step; partial runs cannot pass signoff')
    args = p.parse_args()
    if args.sta_threads < 1:
        p.error('--sta-threads must be positive')
    if sum(bool(x) for x in (args.post_cts_checkpoint, args.post_grt_checkpoint, args.post_rcx_checkpoint)) > 1:
        p.error('Choose only one checkpoint source')
    out, pdks = args.out.resolve(), args.pdk_root.resolve()
    out.mkdir(parents=True, exist_ok=True)
    config = json.loads((ROOT / 'examples/gyro/sky130/config.json').read_text())
    config['VERILOG_FILES'] = ['dir::gyro_core.v']
    rtl = ROOT / 'fpga_lab/hdl/gyro_core.v'
    rtl_hash = hashlib.sha256(rtl.read_bytes()).hexdigest()
    manifest_path = out / 'inputs.json'
    flow_files = {name: ROOT / 'examples/gyro/sky130' / name
                  for name in ('gyro_flow.py', 'electrical_repair.tcl', 'extracted_repair.tcl',
                               'targeted_sizing.tcl')}
    manifest = {'rtl_sha256': rtl_hash, 'image': IMAGE, 'pdk_revision': PDK_REVISION, 'config': config,
                'flow_files_sha256': {name: hashlib.sha256(path.read_bytes()).hexdigest()
                                      for name, path in flow_files.items()}}
    if args.resume:
        if not manifest_path.exists() or json.loads(manifest_path.read_text()) != manifest:
            p.error('Resume inputs differ; use a fresh output directory')
        verify_snapshot(out, manifest)
    else:
        if (out / 'runs/sky130').exists():
            p.error('Output already contains a physical run; use --resume or a fresh directory')
        shutil.copy2(rtl, out / 'gyro_core.v')
        for name, path in flow_files.items():
            shutil.copy2(path, out / name)
        write_json(out / 'config.json', config)
        source = args.post_cts_checkpoint or args.post_grt_checkpoint or args.post_rcx_checkpoint
        if source:
            copy_post_cts_checkpoint(source.resolve(), out, manifest, post_grt=bool(args.post_grt_checkpoint),
                                     post_rcx=bool(args.post_rcx_checkpoint))
        write_json(manifest_path, manifest)
    if not (pdks / 'sky130A').exists():
        write_json(out / 'signoff.json', {'macro_signoff_pass': False, 'tapeout_ready': False,
                                        'error': 'Sky130 PDK not installed', 'expected_pdk_revision': PDK_REVISION})
        p.error('Missing sky130A; install the pinned PDK with ciel (see docs/gyro-design.md)')
    if PDK_REVISION not in (pdks / 'sky130A').resolve().parts:
        p.error('sky130A is not the pinned Ciel revision; use a separate matching PDK root')
    inspected = subprocess.run([args.runtime, 'image', 'inspect', IMAGE], capture_output=True, text=True, check=True)
    image = json.loads(inspected.stdout)[0]
    if args.resume:
        verify_image_identity(out / 'toolchain.json', image['Id'])
    if args.post_cts_checkpoint or args.post_grt_checkpoint or args.post_rcx_checkpoint:
        verify_image_identity((args.post_cts_checkpoint or args.post_grt_checkpoint or args.post_rcx_checkpoint) / 'toolchain.json', image['Id'])
    write_json(out / 'toolchain.json', {'image_id': image['Id'], 'image_digests': image.get('RepoDigests', []),
                                      'pdk_revision': PDK_REVISION, 'container_memory': args.memory, 'cpus': 4,
                                      'sta_threads': args.sta_threads})
    flow = ['python3', 'gyro_flow.py', '--manual-pdk', '--pdk-root', '/pdk', '--jobs', '4', '--run-tag', 'sky130']
    flow += ['--override-config', f'STA_THREADS={args.sta_threads}']
    if args.resume:
        step_id, state = resume_point(out)
        flow += ['--from', step_id, '--with-initial-state', str(state)]
        write_json(out / 'last-resume.json', {'step': step_id, 'state': str(state),
                    'state_sha256': hashlib.sha256((out / state).read_bytes()).hexdigest()})
    elif (out / 'checkpoint-state.json').exists():
        provenance = json.loads((out / 'checkpoint-provenance.json').read_text())
        flow += ['--from', provenance['from_step'], '--with-initial-state', 'checkpoint-state.json']
    if args.to:
        flow += ['--to', args.to]
    flow += ['config.json']
    cmd = [args.runtime, 'run', '--rm', '--pull=never', '--network=none', '--cap-drop=ALL',
           '--security-opt=no-new-privileges', f'--memory={args.memory}', '--cpus=4',
           '-v', f'{out}:/work:rw', '-v', f'{pdks}:/pdk:ro', '-w', '/work', image['Id'], *flow]
    write_json(out / 'signoff.json', {'macro_signoff_pass': False, 'tapeout_ready': False, 'status': 'running'})
    with (out / 'flow.log').open('a' if args.resume else 'w') as log:
        result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=False)
    report = collect(out / 'runs/sky130', result.returncode)
    report['partial_run'] = args.to
    if args.to:
        report['macro_signoff_pass'] = False
    write_json(out / 'signoff.json', report)
    print(json.dumps({k: v for k, v in report.items() if k != 'metrics'}, indent=2))
    return 0 if report['macro_signoff_pass'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
