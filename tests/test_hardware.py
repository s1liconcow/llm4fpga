"""Real tool integration tests. Run with FPGA_LAB_TEST_HARDWARE=1."""
import os

import pytest

from fpga_lab.contracts import TranslationSpec, Vector
from fpga_lab.toolchain import Toolchain
from fpga_lab.vhdl import Proposal, evaluate

pytestmark = [pytest.mark.hardware, pytest.mark.skipif(
    os.getenv('FPGA_LAB_TEST_HARDWARE') != '1', reason='set FPGA_LAB_TEST_HARDWARE=1 to run Podman tools')]

IDENTITY = '''library ieee;
use ieee.std_logic_1164.all;
entity dut is port(clk,rst,in_valid:in std_logic;
input_data:in std_logic_vector(7 downto 0);out_valid:out std_logic;
output_data:out std_logic_vector(7 downto 0));end;
architecture rtl of dut is begin
process(clk) begin if rising_edge(clk) then
if rst='1' then out_valid<='0';output_data<=(others=>'0');
else out_valid<=in_valid;output_data<=input_data;end if;
end if;end process;end;
'''


def test_real_simulation_synthesis_and_mapped_replay(tmp_path):
    spec=TranslationSpec('identity',8,8,4,1,'identity','source.py','dev.json','audit.json')
    result=evaluate(Proposal(IDENTITY,1),spec,[Vector(i,i) for i in range(256)],tmp_path,Toolchain.configured())
    assert result['accepted'], result
    assert result['numeric']['samples']==256
    assert result['post_synthesis']['pass']
    assert result['hardware']['ffs']>0


def test_real_hardware_rejects_false_latency(tmp_path):
    spec=TranslationSpec('identity',8,8,4,1,'identity','source.py','dev.json','audit.json')
    result=evaluate(Proposal(IDENTITY,2),spec,[Vector(i,i) for i in range(16)],tmp_path,Toolchain.configured(),synthesize=False)
    assert not result['accepted']
    assert 'Protocol mismatch' in result['error']


def test_real_slam_mapped_coprocessor(tmp_path):
    import numpy as np
    from fpga_lab.cosim import HardwareSession, build_server
    from fpga_lab.problem import ROOT
    from fpga_lab.slam_kernel import decode_result, oracle, pack, pack_result, specification
    spec = specification()
    proposal = Proposal.parse((ROOT/'examples/slam/recorded_codex.json').read_text())
    rng = np.random.default_rng(39017)
    words = [pack(rng.uniform(-16,16,(n,2)),rng.uniform(-16,16,(n,2)),
                  [*rng.uniform(-16,16,2),rng.uniform(-np.pi,np.pi)],inactive=(1<<1024)-1)
             for n in list(range(9))*3]
    vectors = [Vector(w,pack_result(oracle(w)),tuple(oracle(w))) for w in words]
    candidate = tmp_path/'candidate'
    result = evaluate(proposal,spec,vectors,candidate,Toolchain.configured())
    assert result['accepted'],result
    (candidate/'proposal.json').write_text((ROOT/'examples/slam/recorded_codex.json').read_text())
    server = build_server(candidate,spec,tmp_path/'cosim')
    with HardwareSession(server,spec) as session:
        for word in words:
            actual = decode_result(session.request(word))
            assert actual[-1]==oracle(word)[-1]
            assert np.max(np.abs(actual-oracle(word)))<.02
        assert session.requests==len(words)
        with pytest.raises(ValueError,match='port'):
            session.request(1<<spec.input_bits)


def test_real_mapping_enforces_resource_budget(tmp_path):
    spec = TranslationSpec('budget',8,8,4,1,'identity','source.py','dev.json','audit.json',max_ffs=0)
    result = evaluate(Proposal(IDENTITY,1),spec,[Vector(7,7)],tmp_path,Toolchain.configured())
    assert not result['accepted']
    assert result['numeric']['pass']
    assert 'budget' in result['error'].lower()
