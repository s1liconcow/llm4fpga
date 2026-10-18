"""The physical report must fail closed when evidence is missing or negative."""
import importlib.util
import hashlib
import json
from pathlib import Path
import pytest

MODULE = Path(__file__).resolve().parents[1] / 'scripts/gyro_sky130.py'
spec = importlib.util.spec_from_file_location('gyro_sky130', MODULE)
flow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flow)


def test_signoff_requires_every_metric_and_view(tmp_path):
    assert not flow.collect(tmp_path, 0)['macro_signoff_pass']
    final = tmp_path / 'final'
    final.mkdir()
    metrics = {name: 0 for name in flow.REQUIRED_ZERO}
    for suffix in ('.gds', '.lef', '.v', '.spef', '.sdc'):
        (final / ('test' + suffix)).write_text('test fixture')
    (final / 'metrics.json').write_text(json.dumps(metrics))
    assert flow.collect(tmp_path, 0)['macro_signoff_pass']
    assert flow.collect(tmp_path, 0)['artifact_hashes']['final/test.gds']['bytes'] == 12
    (final / 'test.gds').write_bytes(b'')
    assert not flow.collect(tmp_path, 0)['macro_signoff_pass']
    (final / 'test.gds').write_text('test fixture')
    assert not flow.collect(tmp_path, 1)['macro_signoff_pass']
    assert not flow.collect(tmp_path, 0)['tapeout_ready']
    for required in flow.REQUIRED_ZERO:
        metrics[required] = 1
        (final / 'metrics.json').write_text(json.dumps(metrics))
        assert not flow.collect(tmp_path, 0)['macro_signoff_pass'], required
        del metrics[required]
        (final / 'metrics.json').write_text(json.dumps(metrics))
        assert not flow.collect(tmp_path, 0)['macro_signoff_pass'], required
        metrics[required] = 0


def test_checkpoint_rejects_design_changes_and_preserves_views(tmp_path):
    source, out = tmp_path / 'source', tmp_path / 'new'
    source.mkdir(); out.mkdir()
    original = {'rtl_sha256': 'rtl', 'pdk_revision': 'pdk', 'image': 'image',
                'config': {'CLOCK_PERIOD': 40, 'PL_RESIZER_HOLD_SLACK_MARGIN': .1}}
    (source / 'inputs.json').write_text(json.dumps(original))
    step = source / 'runs/sky130/36-openroad-stamidpnr-1'
    step.mkdir(parents=True)
    (source / 'design.odb').write_text('physical database fixture')
    (step / 'state_out.json').write_text(json.dumps({'odb': '/work/design.odb', 'metrics': {'m': 3}}))
    revised = {**original, 'config': {**original['config'], 'PL_RESIZER_HOLD_SLACK_MARGIN': .03}}
    result = flow.copy_post_cts_checkpoint(source, out, revised)
    assert result['source_inputs'] == original
    assert (out / 'checkpoint/design.odb').read_text() == 'physical database fixture'
    assert json.loads((out / 'checkpoint-state.json').read_text()) == {
        'odb': '/work/checkpoint/design.odb', 'metrics': {'m': 3}}
    revised['rtl_sha256'] = 'other rtl'
    with pytest.raises(ValueError):
        flow.copy_post_cts_checkpoint(source, out, revised)
    revised['rtl_sha256'] = 'rtl'
    revised['config']['CLOCK_PERIOD'] = 50
    with pytest.raises(ValueError):
        flow.copy_post_cts_checkpoint(source, out, revised)
    revised['config']['CLOCK_PERIOD'] = 40
    revised['config']['meta'] = flow.POST_ANTENNA_REPAIR_FLOW
    flow.copy_post_cts_checkpoint(source, out, revised)
    revised['config']['meta'] = {'flow': ['OpenROAD.DetailedRouting']}
    with pytest.raises(ValueError):
        flow.copy_post_cts_checkpoint(source, out, revised)
    revised['config']['meta'] = flow.EXTRACTED_REPAIR_FLOW
    rcx = source / 'runs/sky130/14-openroad-rcx'
    rcx.mkdir()
    state = {'odb': '/work/design.odb'}
    (rcx / 'state_out.json').write_text(json.dumps(state))
    with pytest.raises(ValueError, match='three RC views'):
        flow.copy_post_cts_checkpoint(source, out, revised, post_rcx=True)
    state['spef'] = {}
    for corner in ('min', 'nom', 'max'):
        (source / f'{corner}.spef').write_text(f'{corner} parasitic fixture')
        state['spef'][corner + '_*'] = f'/work/{corner}.spef'
    (rcx / 'state_out.json').write_text(json.dumps(state))
    evidence = flow.copy_post_cts_checkpoint(source, out, revised, post_rcx=True)
    assert evidence['from_step'] == 'Gyro.ExtractedRepair'
    assert (out / 'checkpoint/max.spef').read_text() == 'max parasitic fixture'


def test_resume_uses_last_attempted_step_input(tmp_path):
    for n in (6, 7):
        p = tmp_path / f'runs/sky130/{n:02}-step'
        p.mkdir(parents=True)
        (p / 'config.json').write_text(json.dumps({'meta': {'step': f'OpenROAD.Step{n}'}}))
        (p / 'state_in.json').write_text('{}')
    # An unfinished child must not supersede the enclosing top-level step.
    (p / '1-child').mkdir()
    assert flow.resume_point(tmp_path) == ('OpenROAD.Step7', Path('runs/sky130/07-step/state_in.json'))


def test_resume_rejects_modified_saved_inputs(tmp_path):
    (tmp_path / 'gyro_core.v').write_text('original source')
    (tmp_path / 'flow.py').write_text('original flow')
    (tmp_path / 'config.json').write_text('{"clock": 40}')
    manifest = {'rtl_sha256': hashlib.sha256(b'original source').hexdigest(),
                'flow_files_sha256': {'flow.py': hashlib.sha256(b'original flow').hexdigest()},
                'config': {'clock': 40}}
    flow.verify_snapshot(tmp_path, manifest)
    (tmp_path / 'flow.py').write_text('changed flow')
    with pytest.raises(ValueError, match='flow.py'):
        flow.verify_snapshot(tmp_path, manifest)
    (tmp_path / 'flow.py').write_text('original flow')
    (tmp_path / 'config.json').write_text('{"clock": 50}')
    with pytest.raises(ValueError, match='configuration'):
        flow.verify_snapshot(tmp_path, manifest)


def test_checkpoint_rejects_changed_container_image(tmp_path):
    record = tmp_path / 'toolchain.json'
    record.write_text('{"image_id": "immutable-image-id"}')
    flow.verify_image_identity(record, 'immutable-image-id')
    with pytest.raises(ValueError, match='image identity'):
        flow.verify_image_identity(record, 'different-image-at-same-tag')


def test_package_rejects_failed_checks_and_changed_netlist(tmp_path):
    spec = importlib.util.spec_from_file_location('package_gyro_macro', MODULE.with_name('package_gyro_macro.py'))
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    physical, replay = tmp_path / 'physical', tmp_path / 'replay'
    physical.mkdir(); replay.mkdir()
    (physical / 'signoff.json').write_text(json.dumps({'macro_signoff_pass': False}))
    (physical / 'inputs.json').write_text('{}')
    (replay / 'summary.json').write_text('{}')
    output = tmp_path / 'macro.tar.gz'
    with pytest.raises(ValueError, match='Physical macro'):
        exporter.package(physical, replay, output)
    (physical / 'signoff.json').write_text(json.dumps({'macro_signoff_pass': True, 'flow_exit_code': 0}))
    with pytest.raises(ValueError, match='functional replay'):
        exporter.package(physical, replay, output)
    (replay / 'summary.json').write_text(json.dumps({
        'accepted': True, 'replay': {'pass': True}, 'netlist_sha256': 'previous netlist hash'}))
    netlist = physical / 'runs/sky130/final/nl/design.v'
    netlist.parent.mkdir(parents=True)
    netlist.write_text('changed netlist fixture')
    with pytest.raises(ValueError, match='Replayed netlist differs'):
        exporter.package(physical, replay, output)
    (replay / 'summary.json').write_text(json.dumps({
        'accepted': True, 'replay': {'pass': True}, 'netlist_sha256': exporter.sha256(netlist)}))
    gds = physical / 'runs/sky130/final/design.gds'
    gds.write_text('changed layout fixture')
    (physical / 'signoff.json').write_text(json.dumps({
        'macro_signoff_pass': True, 'flow_exit_code': 0,
        'artifact_hashes': {'final/design.gds': {'bytes': gds.stat().st_size, 'sha256': 'old layout hash'}}}))
    with pytest.raises(ValueError, match='Physical artifact changed'):
        exporter.package(physical, replay, output)
    assert not output.exists()
