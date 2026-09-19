import json
from dataclasses import asdict, replace

import numpy as np
import pytest

from fpga_lab.contracts import OutputField, TranslationSpec, Vector, load_vectors
from fpga_lab.slam import compose, estimate, floating_reduction, load_carmen, raycast, relative, synthetic_dataset, trajectory_error
from fpga_lab.slam_kernel import decode_result, matrices, oracle, pack, pack_result, prepare_slam, source_function, specification, unpack
from fpga_lab.vhdl import score_trace


def test_field_scoring_preserves_signed_lanes_and_independent_rms():
    spec = TranslationSpec('lanes',8,16,2,2,'lanes','src.py','dev.json','audit.json',
        output_fields=(OutputField('x',8,0,scale=16,max_abs_error=.2,max_rms_error=.01),
                       OutputField('y',8,8,scale=4,max_abs_error=1,max_rms_error=1)))
    result = score_trace('0 1 08ff',spec,{0:Vector(0,0x08fe,(-.125,2.))},1)
    assert result['fields']['y']['pass']
    assert result['fields']['x']['max_abs_error']==.0625
    assert not result['pass']  # x RMS fails even though max error passes


def test_exact_field_does_not_lose_low_bits_to_float():
    spec = replace(specification(),output_bits=64,output_fields=(OutputField('wide',64,0,signed=False),))
    result = score_trace(f'0 1 {1<<63:x}',spec,{0:Vector(0,(1<<63)+1,(float(1<<63),))},1)
    assert not result['pass']


def test_fields_cover_bus_once_and_survive_json(tmp_path):
    spec = specification()
    with pytest.raises(ValueError,match='overlap'):
        replace(spec,output_fields=spec.output_fields+(spec.output_fields[0],))
    with pytest.raises(ValueError,match='cover'):
        replace(spec,output_fields=spec.output_fields[:-1])
    path = tmp_path/'spec.json'
    path.write_text(json.dumps(asdict(spec)))
    assert TranslationSpec.load(path).public_contract()==json.loads(json.dumps(spec.public_contract()))
    with pytest.raises(ValueError,match='max_dsps'):
        replace(spec,max_dsps=True)


def test_kernel_oracle_source_and_analytic_gradient():
    rng = np.random.default_rng(621)
    p,q = rng.uniform(-5,5,(2,8,2))
    pose = np.array([.2,-.1,.37])
    word = pack(p,q,pose)
    values = oracle(word)
    assert np.allclose(source_function()(*unpack(word)),values,atol=1e-10)
    assert np.allclose(decode_result(pack_result(values)),values,atol=2e-10)
    # Independently finite-difference the geometric cost to verify g, not only
    # two implementations of the same Jacobian formula.
    baseline = floating_reduction(p,q,pose)
    h,g,_ = matrices(baseline)
    for i in range(3):
        d = np.eye(3)[i]*1e-6
        gradient = (floating_reduction(p,q,pose+d)[6]-floating_reduction(p,q,pose-d)[6])/2e-6
        assert gradient == pytest.approx(2*g[i],rel=1e-7,abs=1e-6)
    assert np.linalg.eigvalsh(h).min()>0


def test_empty_packet_ignores_garbage_and_contract_rejects_out_of_bounds():
    empty = np.empty((0,2))
    assert np.array_equal(oracle(pack(empty,empty,[1,2,3],inactive=(1<<1024)-1)),np.zeros(8))
    with pytest.raises(ValueError,match='outside'):
        pack(np.array([[16.01,0]]),np.zeros((1,2)),[0,0,0])
    with pytest.raises(ValueError,match='0..8'):
        pack(np.zeros((9,2)),np.zeros((9,2)),[0,0,0])


def test_development_audit_disjoint_and_references_load(tmp_path):
    path = prepare_slam(tmp_path)
    spec = TranslationSpec.load(path)
    dev,audit = [load_vectors(tmp_path/name,spec) for name in (spec.vectors,spec.audit_vectors)]
    assert len(dev)==160 and len(audit)==800
    assert not {v.input for v in dev} & {v.input for v in audit}


def test_geometry_and_se2():
    walls = np.array([[[2,-1],[2,1]], [[-1,3],[1,3]]],dtype=float)
    ranges = raycast(np.zeros(2),np.array([0,np.pi/2,np.pi]),walls)
    assert ranges[:2]==pytest.approx([2,3])
    assert np.isinf(ranges[2])
    a,b = np.array([1.,2.,2.9]),np.array([-3.,4.,-2.8])
    assert compose(a,relative(a,b))==pytest.approx(b)


def test_float_slam_builds_map_without_truth():
    pytest.importorskip('scipy')
    data = synthetic_dataset(frames=80,seed=41,beams=120)
    result = estimate(data.scans,data.odometry)
    assert len(result['map'])>200
    assert len(result['rejected_frames'])<4
    assert trajectory_error(result['poses'],data.truth)['position_rmse_m']<.3


def test_degenerate_scan_is_reported_and_does_not_corrupt_map():
    pytest.importorskip('scipy')
    scans = [np.zeros((25,2)),np.zeros((25,2))]
    result = estimate(scans,np.zeros((2,3)))
    assert result['rejected_frames']==[1]
    assert np.all(np.isfinite(result['poses']))


def test_carmen_uses_odometry_not_corrected_pose(tmp_path):
    path = tmp_path/'test.clf'
    ranges = ' '.join(['1']*180)
    path.write_text('\n'.join(f'FLASER 180 {ranges} 999 999 999 {i} 0 0 {100+i} host {i}' for i in range(3)))
    data = load_carmen(path,frames=3,stride=1)
    assert data.truth is None
    assert data.odometry[1:]==pytest.approx(np.array([[1,0,0],[1,0,0]]))
    assert data.scans[0][0]==pytest.approx([0,-1])
    assert data.scans[0][90]==pytest.approx([1,0])
    assert data.scans[0][-1]==pytest.approx([np.cos(np.deg2rad(89)),np.sin(np.deg2rad(89))])
    with pytest.raises(ValueError,match='Only'):
        load_carmen(path,frames=4,stride=1)
