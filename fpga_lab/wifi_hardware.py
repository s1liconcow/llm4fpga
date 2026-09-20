"""Trusted streaming scoreboard for generated Wi-Fi VHDL and mapped circuits."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import shutil
import time

from .problem import digest, write_json
from .toolchain import Toolchain
from .wifi_fixtures import Case

RESOURCE_LIMITS = {
    'compact': {'luts':40000,'ffs':40000,'dsps':100,'bram18_equivalents':100},
    # AMD DS180: 33,650 slices, four LUTs and eight registers per slice.
    'xc7a200t': {'luts':134600,'ffs':269200,'dsps':740,'bram18_equivalents':730},
}

PORTS = '''clk,rst,in_valid,out_ready : in std_logic;
input_data : in std_logic_vector(31 downto 0);
out_valid,out_first,out_last,out_fcs_ok,overflow : out std_logic;
output_data,out_rate : out std_logic_vector(7 downto 0);
out_length : out std_logic_vector(11 downto 0);
debug_valid : out std_logic; debug_tag : out std_logic_vector(7 downto 0);
debug_data : out std_logic_vector(31 downto 0)'''

TESTBENCH = '''library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use ieee.std_logic_textio.all;
use std.textio.all;
use std.env.all;
entity tb is end;
architecture test of tb is
  signal clk : std_logic := '0';
  signal rst,in_valid,out_ready : std_logic := '0';
  signal input_data : std_logic_vector(31 downto 0) := (others=>'0');
  signal out_valid,out_first,out_last,out_fcs_ok,overflow,debug_valid : std_logic;
  signal output_data,out_rate,debug_tag : std_logic_vector(7 downto 0);
  signal out_length : std_logic_vector(11 downto 0);
  signal debug_data : std_logic_vector(31 downto 0);
begin
  clk <= not clk after 2500 ps;
  d: entity work.dut port map(clk,rst,in_valid,out_ready,input_data,
    out_valid,out_first,out_last,out_fcs_ok,overflow,output_data,out_rate,out_length,
    debug_valid,debug_tag,debug_data);
  process
    file stimulus : text open read_mode is "stimulus.txt";
    file trace : text open write_mode is "trace.txt";
    file debug : text open write_mode is "debug.txt";
    variable l,o : line;
    variable r,v,ready,cycle : integer := 0;
    variable data : std_logic_vector(31 downto 0);
    variable held : std_logic_vector(30 downto 0);
    variable stalled : boolean := false;
  begin
    while not endfile(stimulus) loop
      readline(stimulus,l); read(l,r); read(l,v); read(l,ready); hread(l,data);
      wait until falling_edge(clk);
      rst <= std_logic'val(r+2); in_valid <= std_logic'val(v+2);
      out_ready <= std_logic'val(ready+2); input_data <= data;
      wait until rising_edge(clk);
      if r=0 then
        assert overflow='0' report "Input/output storage overflow" severity failure;
        assert out_valid='0' or out_valid='1' report "Unknown output valid" severity failure;
        if stalled then
          assert out_valid='1' and (out_first & out_last & out_fcs_ok & out_rate & out_length & output_data)=held
            report "Output changed while stalled" severity failure;
        end if;
        if out_valid='1' and ready=1 then
          write(o,cycle); write(o,string'(" ")); write(o,out_first);
          write(o,string'(" ")); write(o,out_last); write(o,string'(" ")); write(o,out_fcs_ok);
          write(o,string'(" ")); hwrite(o,out_rate); write(o,string'(" ")); hwrite(o,out_length);
          write(o,string'(" ")); hwrite(o,output_data); writeline(trace,o);
        end if;
        stalled := out_valid='1' and ready=0;
        held := out_first & out_last & out_fcs_ok & out_rate & out_length & output_data;
      else
        stalled := false;
        write(o,string'("RESET ")); write(o,cycle); writeline(trace,o);
      end if;
      wait for 1 ns;
      if r=1 then
        assert out_valid='0' and overflow='0' report "Reset failed" severity failure;
      end if;
      if debug_valid='1' then
        write(o,cycle); write(o,string'(" ")); hwrite(o,debug_tag);
        write(o,string'(" ")); hwrite(o,debug_data); writeline(debug,o);
      end if;
      cycle:=cycle+1;
    end loop;
    report "HARNESS_DONE"; finish;
  end process;
end;
'''

# The driver observes outputs before the active edge, matching ready/valid acceptance.
DRIVER = r'''#include "Vdut.h"
#include "verilated.h"
#include <fstream>
#include <iostream>
#include <iomanip>
#include <cstdint>
int main(int argc,char **argv) {
  Verilated::commandArgs(argc,argv); Vdut d;
  std::ifstream in("stimulus.txt"); std::ofstream out("trace.txt"), dbg("debug.txt");
  int r,v,ready; unsigned word; unsigned long cycle=0; bool stalled=false; uint64_t held=0;
  while(in>>std::dec>>r>>v>>ready>>std::hex>>word) {
    d.clk=0;d.rst=r;d.in_valid=v;d.out_ready=ready;d.input_data=word;d.eval();
    Verilated::timeInc(2500);
    uint64_t packed=(uint64_t(d.out_first)<<30)|(uint64_t(d.out_last)<<29)|(uint64_t(d.out_fcs_ok)<<28)|
                    (uint64_t(d.out_rate)<<20)|(uint64_t(d.out_length)<<8)|d.output_data;
    if(!r) {
      if(d.overflow) {std::cerr<<"overflow at "<<cycle;return 2;}
      if(stalled && (!d.out_valid||held!=packed)) {std::cerr<<"unstable stalled output";return 2;}
      if(d.out_valid && ready) out<<std::dec<<cycle<<" "<<int(d.out_first)<<" "<<int(d.out_last)<<" "<<int(d.out_fcs_ok)
        <<" "<<std::hex<<int(d.out_rate)<<" "<<int(d.out_length)<<" "<<int(d.output_data)<<"\n";
      stalled=d.out_valid&&!ready;held=packed;
    } else {stalled=false;out<<"RESET "<<std::dec<<cycle<<"\n";}
    d.clk=1;d.eval();Verilated::timeInc(2500);
    if(r && (d.out_valid||d.overflow)) {std::cerr<<"reset failed";return 2;}
    if(d.debug_valid) dbg<<std::dec<<cycle<<" "<<std::hex<<int(d.debug_tag)<<" "<<unsigned(d.debug_data)<<"\n";
    ++cycle;
  }
  d.final(); return 0;
}'''


def validate(source: str) -> None:
    if not 100 <= len(source) <= 1_000_000:
        raise ValueError('Candidate size outside bounds')
    clean = re.sub(r'"(?:[^"]|"")*"|--[^\n]*',lambda m:'' if m.group().startswith('--') else m.group(),source)
    if not re.search(r'\bentity\s+dut\s+is\b',clean,re.I):
        raise ValueError('Top entity must be dut')
    if re.search(r'\b(file|textio|foreign|access|external|configuration|report|assert|wait|after|attribute|real|math_real)\b',clean,re.I):
        raise ValueError('Forbidden non-hardware construct in generated RTL')
    for lib in re.findall(r'\blibrary\s+([^;]+);',clean,re.I):
        if any(x.strip().lower() not in ('ieee','work') for x in lib.split(',')):
            raise ValueError('Only IEEE and generated work units are allowed')


def stimulus(case: Case, folder: Path) -> int:
    cycle = 0
    with (folder/'stimulus.txt').open('w') as f:
        def emit(r=0,v=0,word=0):
            nonlocal cycle
            ready = int(not case.stalls or cycle%257>=32)
            f.write(f'{r} {v} {ready} {word:08x}\n'); cycle+=1
        for _ in range(5):emit(1)
        for i,sample in enumerate(case.samples):
            if i == case.reset_at:
                for _ in range(3):emit(1)
            word = ((int(sample.real)&65535)<<16)|(int(sample.imag)&65535)
            emit(0,1,word)
            for _ in range(9):emit()
        for _ in range(50000):emit()
    return cycle


def scoreboard(text: str, case: Case) -> dict:
    frames=[]; pending=None; last_cycle=-1
    for row in text.splitlines():
        if row.startswith('RESET '):
            pending=None
            continue
        fields=row.split()
        if len(fields)!=7 or any(re.fullmatch('[0-9a-fA-F]+',x) is None for x in fields):
            raise ValueError(f'Malformed or unknown output: {row[:120]}')
        cycle=int(fields[0]); first,last,crc=map(int,fields[1:4]); rate,length,byte=[int(x,16) for x in fields[4:]]
        if cycle<=last_cycle or first not in (0,1) or last not in (0,1) or crc not in (0,1) or byte>255:
            raise ValueError('Malformed packet protocol')
        last_cycle=cycle
        if first:
            if pending is not None:raise ValueError('New packet before previous last')
            pending={'rate':rate,'length':length,'data':bytearray(),'first_cycle':cycle}
        if pending is None:raise ValueError('Packet byte without first')
        if (rate,length)!=(pending['rate'],pending['length']):raise ValueError('Metadata changed within packet')
        pending['data'].append(byte)
        if last:
            pending.update(fcs_ok=bool(crc),last_cycle=cycle)
            if len(pending['data'])!=length:raise ValueError('Output length does not match header')
            frames.append(pending);pending=None
    if pending is not None:raise ValueError('Truncated output packet')
    expected=list(case.expected)
    mismatches=[]; matched=0
    for actual in frames:
        while expected and not expected[0].fcs_ok and bytes(actual['data']) != expected[0].data:
            expected.pop(0)  # Contract permits dropping checksum-invalid frames.
        if not expected:
            mismatches.append({'error':'unexpected frame','rate':actual['rate'],'length':actual['length']});continue
        want=expected.pop(0)
        if bytes(actual['data'])!=want.data or actual['rate']!=want.rate or actual['fcs_ok']!=want.fcs_ok:
            first_difference=next((i for i,(a,b) in enumerate(zip(actual['data'],want.data)) if a!=b),None)
            mismatches.append({'error':'frame mismatch','expected_rate':want.rate,'actual_rate':actual['rate'],
                'expected_length':len(want.data),'actual_length':actual['length'],'first_byte_difference':first_difference,
                'expected_prefix':want.data[:32].hex(),'actual_prefix':bytes(actual['data'][:32]).hex(),
                'expected_fcs':want.fcs_ok,'actual_fcs':actual['fcs_ok']})
        else:matched+=1
    for p in expected:
        if p.fcs_ok:mismatches.append({'error':'missing frame','rate':p.rate,'length':len(p.data),'start':p.start})
    throughput={}
    if case.provenance.get('check_sustained_rate') and len(frames)==len(case.expected) and len(frames)>=16:
        # A FIFO can hide a throughput deficit for many packets. Measure whether
        # processing lag grows after startup, even when all output bytes match.
        lags=[f['last_cycle']-p.end*10 for f,p in zip(frames,case.expected)]
        growth=sum(lags[-4:])/4-sum(lags[4:8])/4
        throughput={'lag_growth_cycles':growth,
            'input_cycles_per_packet':(case.expected[-1].end-case.expected[4].end)*10/(len(frames)-5),
            'output_cycles_per_packet':(frames[-1]['last_cycle']-frames[4]['last_cycle'])/(len(frames)-5)}
        if growth>case.provenance['max_lag_growth_cycles']:
            mismatches.append({'error':'Processing falls behind sustained input',**throughput})
    return {'pass':not mismatches,'expected_good_frames':sum(p.fcs_ok for p in case.expected),
            'output_frames':len(frames),'matched_frames':matched,'output_bytes':sum(len(f['data']) for f in frames),
            'failures':mismatches[:8], 'throughput':throughput,
            'frames':[dict(f,data_hex=bytes(f['data']).hex())|{'data':None} for f in frames]}


def simulate(folder: Path, cases: list[Case], tools: Toolchain, *, mapped: bool = False) -> dict:
    jobs=min(4,max(1,int(os.getenv('FPGA_LAB_WIFI_JOBS','4')))) if mapped else 1
    if jobs>1 and len(cases)>1:
        def run_one(item):
            index,case=item
            work=folder/'mapped-cases'/f'{index:02d}-{case.name}'
            (work/'obj_dir').mkdir(parents=True,exist_ok=True)
            executable=work/'obj_dir/wifi_sim'
            executable.unlink(missing_ok=True)
            os.link(folder/'obj_dir/wifi_sim',executable)
            return index,simulate(work,[case],tools,mapped=True)['results'][0]
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            # Start the longest independent streams first; report in fixture order.
            ordered=sorted(enumerate(cases),key=lambda item:len(item[1].samples),reverse=True)
            indexed=list(pool.map(run_one,ordered))
        rows=[row for _,row in sorted(indexed)]
        return {'pass':all(r['pass'] for r in rows),'cases_run':len(rows),
                'cases_total':len(cases),'simulation_jobs':jobs,'results':rows}
    rows=[]
    for case in cases:
        started=time.monotonic()
        total=stimulus(case,folder)
        # Keep file identity stable across macOS/Podman shared-filesystem mounts.
        # Truncation also prevents an old successful trace surviving a failed run.
        (folder/'trace.txt').write_text('')
        (folder/'debug.txt').write_text('')
        try:
            tools.run(['./obj_dir/wifi_sim'] if mapped else
                      ['ghdl','-r','--std=08','tb','--assert-level=error','--stop-delta=10000'],
                      folder,timeout=1800 if mapped else 600,log='simulation.log')
            result=scoreboard((folder/'trace.txt').read_text(),case)
        except Exception as exc:
            result={'pass':False,'error':str(exc)[-5000:]}
        result.update(name=case.name,cycles=total,elapsed_seconds=time.monotonic()-started)
        debug=(folder/'debug.txt').read_text() if (folder/'debug.txt').exists() else ''
        result['debug_tail']=debug.splitlines()[-30:]
        tag='mapped' if mapped else 'rtl'
        write_json(folder/f'{tag}-{case.name}.json',result)
        rows.append(result)
        print(f'  {tag} {case.name}: {result["pass"]}',flush=True)
        if not result['pass']:break
    return {'pass':len(rows)==len(cases) and all(r['pass'] for r in rows),
            'cases_run':len(rows),'cases_total':len(cases),'results':rows}


def synthesize_receiver(folder: Path, tools: Toolchain) -> dict:
    """Map an already written candidate and return measured Xilinx resources."""
    formal = os.getenv('FPGA_LAB_WIFI_FORMAL') == '1'
    # ABC9's global proof can take much longer than mapping on a large arithmetic
    # circuit. The default acceptance path uses independent mapped simulation.
    # Retain the standard mapping/optimization flow; record the proof setting.
    (folder/'mapping.ys').write_text(
        f'scratchpad -set abc9.verify {int(formal)}\nread_verilog netlist.v\n'
        'synth_xilinx -flatten -family xc7 -top dut -noiopad\n'+
        'check -assert\nwrite_json netlist.json\nwrite_verilog -noattr mapped.v\n')
    # GHDL emits many small writes. Buffer on Linux tmpfs before copying across
    # the macOS/Podman shared filesystem.
    tools.run(['sh','-ec','ghdl --synth --std=08 --out=verilog candidate.vhd -e dut > /tmp/wifi-netlist.v\n'
        'cp /tmp/wifi-netlist.v netlist.v\n'
        'yosys -Q -T -s mapping.ys'],
        folder,timeout=1800,log='synthesis.log')
    return mapped_resources(folder, formal=formal)


def mapped_resources(folder: Path, *, formal: bool = False) -> dict:
    """Count technology cells, including distributed RAM's occupied LUTs."""
    net=json.loads((folder/'netlist.json').read_text()); counts={}
    for cell in net['modules']['dut']['cells'].values():
        name=cell['type'];counts[name]=counts.get(name,0)+1
    unmapped={name:count for name,count in counts.items()
              if name.startswith('$') and name not in ('$scopeinfo','$buf')}
    if unmapped:
        raise ValueError(f'Unmapped logic remains after FPGA mapping: {unmapped}')
    logic_luts=sum(v for k,v in counts.items() if re.fullmatch(r'LUT[1-6]',k))
    # RAM64M occupies all four LUTs in one SLICEM (AMD UG474/UG953).
    ram_lut_weights={'RAM32X1S':1,'RAM32X1D':2,'RAM32M':4,'RAM64X1S':1,
        'RAM64X1D':2,'RAM64M':4,'RAM128X1S':2,'RAM128X1D':4,'RAM256X1S':4}
    unknown_ram=[k for k in counts if k.startswith('RAM') and not k.startswith('RAMB') and k not in ram_lut_weights]
    if unknown_ram:raise ValueError(f'Unknown distributed RAM resource cost: {unknown_ram}')
    ram_luts=sum(counts.get(k,0)*v for k,v in ram_lut_weights.items())
    shift_luts=sum(v for k,v in counts.items() if k.startswith('SRL'))
    hardware={'luts':logic_luts+ram_luts+shift_luts,'logic_luts':logic_luts,
        'distributed_ram_luts':ram_luts,'shift_register_luts':shift_luts,
        'ffs':sum(v for k,v in counts.items() if k.startswith('FD')),
        'dsps':sum(v for k,v in counts.items() if k.startswith('DSP')),
        'bram18_equivalents':sum((2 if '36' in k else 1)*v for k,v in counts.items() if k.startswith('RAMB')),
        'cells':counts,'stage':'yosys-xilinx-technology-mapping',
        'abc9_formal_equivalence_requested':formal}
    return hardware


def build_mapped(folder: Path, tools: Toolchain) -> None:
    (folder/'driver.cpp').write_text(DRIVER)
    # Yosys supplies a declaration, not behavior, for RAMB36E1. Use the
    # unmodified Apache-licensed Xilinx functional model for mapped memory.
    tools.run(['cp','/usr/share/yosys/xilinx/cells_sim.v','xilinx_cells.v'],folder,log='models.log')
    cells=(folder/'xilinx_cells.v').read_text()
    cells,count=re.subn(r'\bmodule RAMB36E1\s*\([\s\S]*?\bendmodule\b','',cells)
    if count!=1:raise ValueError('Expected exactly one Yosys RAMB36E1 declaration')
    (folder/'xilinx_cells.v').write_text(cells)
    shutil.copyfile(Path(__file__).parent/'hdl/RAMB36E1.v',folder/'RAMB36E1.v')
    write_json(folder/'simulation-models.json',{
        'RAMB36E1':{'source':'Xilinx/XilinxUnisimLibrary',
            'commit':'1c8e05fd1e9a79ceb8b996a0996674122eed086f',
            'sha256':digest((folder/'RAMB36E1.v').read_bytes())},
        'other_primitives':'Yosys xilinx/cells_sim.v',
        'primitive_delays_enabled':False,'global_configuration_reset':0})
    (folder/'sim_top.v').write_text('''`timescale 1ps/1ps
module glbl;
  wire GSR = 1'b0;
endmodule
module sim_top(input clk,rst,in_valid,out_ready, input [31:0] input_data,
  output out_valid,out_first,out_last,out_fcs_ok,overflow,
  output [7:0] output_data,out_rate, output [11:0] out_length,
  output debug_valid, output [7:0] debug_tag, output [31:0] debug_data);
  glbl glbl();
  dut core(.*);
endmodule
''')
    # Release Verilator's elaboration memory before launching C++ compilers.
    # Disable automatic regrouping so the split files also bound compiler memory.
    tools.run(['verilator','--cc','--exe','--no-timing','-Wno-fatal','--top-module','sim_top','--prefix','Vdut',
        '--output-groups','0','--output-split','10000','--output-split-cfuncs','1000',
        '--Mdir','obj_dir','-o','wifi_sim','sim_top.v','mapped.v','driver.cpp','xilinx_cells.v','RAMB36E1.v'],
        folder,timeout=1800,log='mapped-elaborate.log')
    tools.run(['make','-C','obj_dir','-f','Vdut.mk','-j2'],
        folder,timeout=1800,log='mapped-build.log')


def evaluate(source: str, folder: Path, cases: list[Case], *, synthesize: bool = True,
             target: str = 'compact') -> dict:
    folder.mkdir(parents=True,exist_ok=True)
    result={'accepted':False,'vhdl_sha256':digest(source.encode()),'physical_timing_measured':False,
            'verification_scope':'unverified',
            'requested_verification_scope':'rtl-and-xilinx-mapped' if synthesize else 'rtl-only',
            'resource_target':target,'resource_limits':RESOURCE_LIMITS[target]}
    tools=Toolchain.configured()
    try:
        validate(source)
        (folder/'candidate.vhd').write_text(source)
        # Named association allows generators to choose port declaration order.
        mapping=', '.join(f'{name}=>{name}' for name in ['clk','rst','in_valid','out_ready','input_data',
            'out_valid','out_first','out_last','out_fcs_ok','overflow','output_data','out_rate','out_length',
            'debug_valid','debug_tag','debug_data'])
        tb=re.sub(r'd: entity work.dut port map\([\s\S]*?\);',f'd: entity work.dut port map({mapping});',TESTBENCH)
        (folder/'tb.vhd').write_text(tb)
        tools.run(['sh','-ec','ghdl -a --std=08 candidate.vhd tb.vhd\nghdl -e --std=08 tb'],folder,log='compile.log')
        result['rtl']=simulate(folder,cases,tools)
        result['verification_scope']='rtl-only'
        if not result['rtl']['pass']:raise ValueError('RTL packet/protocol verification failed')
        if synthesize:
            print('  Mapping receiver to Xilinx 7-series primitives...',flush=True)
            hardware=synthesize_receiver(folder,tools)
            result['hardware']=hardware
            for key,limit in RESOURCE_LIMITS[target].items():
                if hardware[key]>limit:raise ValueError(f'{key} budget exceeded: {hardware[key]} > {limit}')
            print('  Building the mapped simulator (tool logs in the output directory)...',flush=True)
            build_mapped(folder,tools)
            print(f'  Replaying {len(cases)} mapped test streams...',flush=True)
            result['mapped']=simulate(folder,cases,tools,mapped=True)
            result['verification_scope']='rtl-and-xilinx-mapped'
            result['simulation_models']=json.loads((folder/'simulation-models.json').read_text())
            if not result['mapped']['pass']:raise ValueError('Mapped packet/protocol verification failed')
        result['accepted']=True
    except Exception as exc:
        result['error']=str(exc)[-10000:]
    write_json(folder/'result.json',result)
    return result
