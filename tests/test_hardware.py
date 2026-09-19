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
