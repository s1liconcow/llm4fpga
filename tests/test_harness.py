import json
from dataclasses import replace

import pytest

from fpga_lab.contracts import TranslationSpec, Vector, load_vectors
from fpga_lab.vhdl import Proposal, score_trace, stimulus, validate_vhdl


def spec(**kwargs):
    return replace(TranslationSpec('test',8,8,8,1,'identity','source.py','dev.json','audit.json'),**kwargs)


def trace(cycles, expected):
    return '\n'.join(f'{i} {int(i in expected)} {expected[i].output if i in expected else 0:02x}'
                     for i in range(len(cycles)))


def test_scoreboard_checks_reset_latency_and_bubbles():
    contract = spec()
    vectors = [Vector(i,i) for i in range(25)]
    cycles, expected = stimulus(contract,vectors,4)
    assert len(expected) == len(vectors)  # interrupted preamble was canceled
    result = score_trace(trace(cycles,expected),contract,expected,len(cycles))
    assert result['pass']
    delayed = {i+1:v for i,v in expected.items()}
    with pytest.raises(ValueError,match='Protocol mismatch'):
        score_trace(trace(cycles,delayed),contract,expected,len(cycles))
    # A design that fails to flush its pending result on reset must fail.
    leaking = dict(expected)
    leaking[6] = vectors[0]
    with pytest.raises(ValueError,match='Protocol mismatch'):
        score_trace(trace(cycles,leaking),contract,expected,len(cycles))


def test_full_width_hash_comparison_does_not_convert_to_float():
    contract = spec(output_bits=256)
    vector = Vector(0,(1<<255)+1)
    expected={0:vector}
    result=score_trace(f'0 1 {1<<255:064x}',contract,expected,1)
    assert not result['pass']
    assert result['violations']==1


def test_missing_or_unknown_outputs_fail():
    contract=spec()
    with pytest.raises(ValueError,match='Incomplete'):
        score_trace('',contract,{0:Vector(0,0)},1)
    with pytest.raises(ValueError,match='Unknown'):
        score_trace('0 1 XX',contract,{0:Vector(0,0)},1)


def test_scaled_signed_errors_and_rms_limit():
    contract=spec(output_signed=True,output_scale=16,max_abs_error=0.1,max_rms_error=0.01)
    result=score_trace('0 1 ff',contract,{0:Vector(0,255,-0.125)},1)
    assert result['max_abs_error']==0.0625
    assert result['violations']==0
    assert not result['pass']  # RMS still exceeds its independent bound


@pytest.mark.parametrize('danger', ['file f: text;', 'use std.textio.all;', 'attribute foreign: string;',
                                    'report "0 1 1234";', 'wait for 1 ns;', 'library other;'])
def test_candidate_cannot_use_harness_hooks(danger):
    with pytest.raises(ValueError):
        validate_vhdl('entity dut is end; architecture rtl of dut is '+danger+' begin end;')


def test_strict_proposal_and_vector_validation(tmp_path):
    with pytest.raises(ValueError):
        Proposal.parse('{"vhdl":"x","latency":true}')
    path=tmp_path/'vectors.json'
    path.write_text(json.dumps([{'input':'0x100','output':'0x00'}]))
    with pytest.raises(ValueError,match='input'):
        load_vectors(path,spec())
    path.write_text(json.dumps([{'input':'0x00','output':'0x00','chain':True}]))
    with pytest.raises(ValueError,match='feedback'):
        load_vectors(path,spec())
