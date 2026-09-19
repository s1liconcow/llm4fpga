"""Trusted VHDL scoreboard, GHDL synthesis and Xilinx technology mapping."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from .contracts import TranslationSpec, Vector
from .problem import digest, write_json
from .toolchain import Toolchain


@dataclass(frozen=True)
class Proposal:
    vhdl: str
    latency: int
    notes: str = ''

    @classmethod
    def parse(cls, text: str) -> 'Proposal':
        obj = json.loads(text)
        if not isinstance(obj.get('vhdl'), str) or type(obj.get('latency')) is not int:
            raise ValueError('Expected JSON with vhdl string and latency integer')
        return cls(obj['vhdl'], obj['latency'], str(obj.get('notes', ''))[:8000])


def validate_vhdl(source: str) -> None:
    if not 50 <= len(source) <= 500_000:
        raise ValueError('VHDL source must be 50..500000 characters')
    # Remove comments only outside quoted strings (a string may contain "--").
    clean = re.sub(r'"(?:[^"]|"")*"|--[^\n]*',
                   lambda m: '' if m.group().startswith('--') else m.group(), source)
    if not re.search(r'\bentity\s+dut\s+is\b', clean, re.I):
        raise ValueError('Top entity must be dut')
    if re.search(r'\b(file|textio|foreign|access|external|configuration|report|assert|wait|after)\b', clean, re.I):
        raise ValueError('Simulation, file, external and reporting constructs are forbidden in candidate RTL')
    if re.search(r'\b(use|library)\s+(std|work)\b', clean, re.I):
        raise ValueError('Only self-contained RTL with IEEE libraries is allowed')
    if re.search(r'\battribute\b', clean, re.I):
        raise ValueError('User-defined attributes are unsupported in generated RTL')
    for library in re.findall(r'\blibrary\s+([^;]+);', clean, re.I):
        if library.strip().lower() != 'ieee':
            raise ValueError('Only IEEE libraries are allowed')


def stimulus(spec: TranslationSpec, vectors: list[Vector], latency: int) -> tuple[list[tuple], dict]:
    """Build a cycle-level expected-valid map, including bubbles and an interrupted job."""
    cycles: list[tuple[int, int, Vector | None]] = []
    expected: dict[int, Vector] = {}
    pending: dict[int, Vector] = {}

    def add(rst=0, vector=None):
        cycle = len(cycles)
        cycles.append((rst, int(vector is not None), vector))
        if rst:
            pending.clear()
        elif vector is not None:
            pending[cycle + latency - 1] = vector
        if cycle in pending:
            expected[cycle] = pending.pop(cycle)

    add(rst=1, vector=vectors[0])  # Reset must dominate an asserted in_valid.
    add(rst=1)
    add(rst=1)
    # Reset during computation, then hold reset; no pre-reset result may leak out.
    add(vector=vectors[0] if latency > 1 else None)
    for _ in range(max(0, latency // 2 - 1)):
        add()
    add(rst=1)
    add(rst=1)
    for _ in range(latency + 2):
        add()
    for i, vector in enumerate(vectors):
        add(vector=vector)
        # Exercise minimum II as well as nonuniform idle gaps.
        for _ in range(spec.initiation_interval - 1 + (3 if i % 11 == 7 else 0)):
            add()
    for _ in range(latency + 3):
        add()
    return cycles, expected


def testbench(spec: TranslationSpec, cycles: list[tuple]) -> tuple[str, str]:
    digits = (spec.input_bits + 3) // 4
    lines = [f'{rst} {valid} {int(v.chain) if v else 0} {v.input if v else 0:0{digits}x}'
             for rst, valid, v in cycles]
    feedback = (f'data({spec.input_bits-1} downto {spec.input_bits-spec.feedback_bits}) := previous;'
                if spec.feedback_bits else 'null;')
    tb = f'''library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use ieee.std_logic_textio.all;
use std.textio.all;
use std.env.all;
entity tb is end;
architecture test of tb is
  signal clk : std_logic := '0';
  signal rst, in_valid : std_logic := '0';
  signal input_data : std_logic_vector({spec.input_bits-1} downto 0) := (others => '0');
  signal out_valid : std_logic;
  signal output_data : std_logic_vector({spec.output_bits-1} downto 0);
begin
  clk <= not clk after 5 ns;
  dut_i: entity work.dut port map(clk => clk, rst => rst, in_valid => in_valid,
    input_data => input_data, out_valid => out_valid, output_data => output_data);
  process
    file stim : text open read_mode is "stimulus.txt";
    file trace : text open write_mode is "trace.txt";
    variable l, o : line;
    variable reset_i, valid_i, chain_i, cycle : integer := 0;
    variable data : std_logic_vector({spec.input_bits-1} downto 0);
    variable previous : std_logic_vector({spec.output_bits-1} downto 0) := (others => '0');
  begin
    while not endfile(stim) loop
      readline(stim,l); read(l,reset_i); read(l,valid_i); read(l,chain_i); hread(l,data);
      wait until falling_edge(clk);
      if chain_i = 1 then {feedback} end if;
      if reset_i = 1 then rst <= '1'; else rst <= '0'; end if;
      if valid_i = 1 then in_valid <= '1'; else in_valid <= '0'; end if;
      input_data <= data;
      wait until rising_edge(clk); wait for 1 ns;
      write(o,cycle); write(o,string'(" ")); write(o,out_valid);
      write(o,string'(" ")); hwrite(o,output_data); writeline(trace,o);
      if out_valid = '1' then previous := output_data; end if;
      cycle := cycle + 1;
    end loop;
    report "HARNESS_DONE"; finish;
  end process;
end;
'''
    return '\n'.join(lines) + '\n', tb


def score_trace(text: str, spec: TranslationSpec, expected: dict[int, Vector], count: int) -> dict:
    rows = text.splitlines()
    if len(rows) != count:
        raise ValueError(f'Incomplete simulation trace: {len(rows)}/{count} cycles')
    errors = []
    failures = []
    mismatches = 0
    samples = 0
    exact = spec.max_abs_error == 0 and spec.max_rms_error == 0
    for cycle, row in enumerate(rows):
        fields = row.split()
        if len(fields) != 3 or fields[0] != str(cycle):
            raise ValueError(f'Malformed trace at cycle {cycle}')
        valid = fields[1]
        want = cycle in expected
        if valid != ('1' if want else '0'):
            raise ValueError(f'Protocol mismatch at cycle {cycle}: out_valid={valid}, expected={int(want)}')
        if not want:
            continue
        if not re.fullmatch(r'[0-9a-fA-F]+', fields[2]):
            raise ValueError(f'Unknown output bits at cycle {cycle}: {fields[2]}')
        value = int(fields[2], 16)
        vector = expected[cycle]
        samples += 1
        if exact:
            error = 0.0 if value == vector.output else 1.0
            bad = value != vector.output
        else:
            signed = value - (1 << spec.output_bits) if spec.output_signed and value >> (spec.output_bits-1) else value
            error = abs(signed / spec.output_scale - float(vector.reference))
            bad = error > spec.max_abs_error
        errors.append(error)
        if bad:
            mismatches += 1
            if len(failures) < 8:
                failures.append({'cycle': cycle, 'input': hex(vector.input), 'expected': hex(vector.output),
                                 'actual': hex(value), 'error': error, 'label': vector.label})
    maximum = max(errors)
    rms = math.sqrt(sum(e*e for e in errors) / len(errors))
    return {'pass': mismatches == 0 and (exact or rms <= spec.max_rms_error),
            'samples': samples, 'cycles': count, 'violations': mismatches,
            'max_abs_error': maximum, 'rms_error': rms, 'comparison': 'bit-exact' if exact else 'scaled-numerical',
            'failures': failures, 'protocol_pass': True}


def export_vivado(folder: Path, spec: TranslationSpec) -> None:
    (folder / 'clock.xdc').write_text(f'create_clock -name clk -period {1000/spec.clock_mhz:.6f} [get_ports clk]\n')
    (folder / 'vivado.tcl').write_text('''# Usage: vivado -mode batch -source vivado.tcl -tclargs xc7a35tcpg236-1
# Out-of-context IP synthesis: choose the actual part for your board.
if {$argc != 1} { error "Pass the FPGA part as the sole argument" }
set here [file dirname [file normalize [info script]]]
set part [lindex $argv 0]
create_project -in_memory -part $part
read_vhdl -vhdl2008 [file join $here candidate.vhd]
read_xdc [file join $here clock.xdc]
synth_design -top dut -part $part -mode out_of_context
report_utilization -file [file join $here vivado_utilization.rpt]
report_timing_summary -file [file join $here vivado_timing.rpt]
write_checkpoint -force [file join $here dut_synth.dcp]
# Pin assignments / board integration are needed before implementation and bitstream generation.
''')


def gate_testbench(spec: TranslationSpec, count: int) -> str:
    feedback = (f'data[{spec.input_bits-1}:{spec.input_bits-spec.feedback_bits}] = previous;'
                if spec.feedback_bits else '')
    return f'''module tb;
reg clk=0, rst=0, in_valid=0;
reg [{spec.input_bits-1}:0] input_data=0, data;
wire out_valid;
wire [{spec.output_bits-1}:0] output_data;
reg [{spec.output_bits-1}:0] previous=0;
integer stim, trace, scan, reset_i, valid_i, chain_i, cycle;
dut d(.clk(clk),.rst(rst),.in_valid(in_valid),.input_data(input_data),
      .out_valid(out_valid),.output_data(output_data));
always #5 clk=~clk;
initial begin
  stim=$fopen("stimulus.txt","r"); trace=$fopen("gate_trace.txt","w");
  for(cycle=0; cycle<{count}; cycle=cycle+1) begin
    scan=$fscanf(stim,"%d %d %d %h\\n",reset_i,valid_i,chain_i,data);
    if(scan != 4) $fatal(1,"Invalid stimulus");
    @(negedge clk);
    if(chain_i==1) begin {feedback} end
    rst=reset_i; in_valid=valid_i; input_data=data;
    @(posedge clk); #1;
    $fdisplay(trace,"%0d %b %h",cycle,out_valid,output_data);
    if(out_valid===1'b1) previous=output_data;
  end
  $fclose(stim); $fclose(trace); $finish;
end
endmodule
'''


def evaluate(proposal: Proposal, spec: TranslationSpec, vectors: list[Vector], folder: Path,
             tools: Toolchain, *, synthesize: bool = True) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    result = {'accepted': False, 'latency': proposal.latency, 'vhdl_sha256': digest(proposal.vhdl.encode()),
              'test_vectors': len(vectors), 'unique_inputs': len({v.input for v in vectors}),
              'synthesis_requested': synthesize, 'target': 'xilinx-7series', 'physical_timing_measured': False}
    try:
        validate_vhdl(proposal.vhdl)
        if not 1 <= proposal.latency <= spec.max_latency:
            raise ValueError('Latency outside contract')
        if any(v.chain for v in vectors) and spec.initiation_interval < proposal.latency:
            raise ValueError('Feedback chaining requires initiation_interval >= latency')
        (folder / 'candidate.vhd').write_text(proposal.vhdl)
        cycles, expected = stimulus(spec, vectors, proposal.latency)
        stim, tb = testbench(spec, cycles)
        (folder / 'stimulus.txt').write_text(stim)
        (folder / 'tb.vhd').write_text(tb)
        # Stale outputs from a prior run must never count as current evidence.
        (folder / 'trace.txt').unlink(missing_ok=True)
        command = ['sh', '-ec', 'ghdl -a --std=08 candidate.vhd tb.vhd\n'
                   'ghdl -e --std=08 tb\nghdl -r --std=08 tb --assert-level=error --stop-delta=10000']
        tools.run(command, folder, log='simulation.log')
        result['numeric'] = score_trace((folder / 'trace.txt').read_text(), spec, expected, len(cycles))
        if result['numeric']['pass']:
            if synthesize:
                tools.run(['sh', '-ec', 'ghdl --synth --std=08 --out=verilog candidate.vhd -e dut > netlist.v\n'
                           'yosys -Q -T -p "read_verilog netlist.v; synth_xilinx -family xc7 -top dut -noiopad; '
                           'check -assert; write_json netlist.json; write_verilog -noattr mapped.v"'], folder, timeout=300, log='synthesis.log')
                net = json.loads((folder / 'netlist.json').read_text())
                counts: dict[str, int] = {}
                for cell in net['modules']['dut']['cells'].values():
                    key = cell['type']
                    counts[key] = counts.get(key, 0) + 1
                result['hardware'] = {'cells': counts, 'luts': sum(v for k,v in counts.items() if re.fullmatch(r'LUT[1-6]', k)),
                                      'ffs': sum(v for k,v in counts.items() if k.startswith('FD')),
                                      'dsps': sum(v for k,v in counts.items() if k.startswith('DSP')),
                                      'brams': sum(v for k,v in counts.items() if k.startswith('RAMB')),
                                      'stage': 'yosys-xilinx-technology-mapping', 'vivado_executed': False}
                (folder / 'gate_tb.v').write_text(gate_testbench(spec, len(cycles)))
                (folder / 'gate_trace.txt').unlink(missing_ok=True)
                # GHDL above checks four-state behavior. Verilator compiles the mapped circuit
                # for fast full-vector replay; using a two-state simulator is explicit evidence.
                tools.run(['sh', '-ec', 'verilator --binary --timing -j 2 -Wno-fatal --top-module tb '
                           '--Mdir obj_dir -o gate_sim mapped.v gate_tb.v '
                           '/usr/share/yosys/xilinx/cells_sim.v\n./obj_dir/gate_sim'],
                          folder, timeout=300, log='gate_simulation.log')
                result['post_synthesis'] = score_trace((folder / 'gate_trace.txt').read_text(),
                                                       spec, expected, len(cycles))
                result['post_synthesis']['simulator'] = 'verilator (two-state); RTL checked by GHDL (four-state)'
                if not result['post_synthesis']['pass']:
                    raise ValueError('Post-synthesis simulation failed')
            result['accepted'] = True
            export_vivado(folder, spec)
    except Exception as exc:
        result['error'] = str(exc)[-8000:]
    write_json(folder / 'result.json', result)
    return result
