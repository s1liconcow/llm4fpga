"""Streaming FEC block verification independent of generated survivor logic."""
from __future__ import annotations
from pathlib import Path
import json
import numpy as np

from .problem import digest,write_json
from .toolchain import Toolchain
from .wifi_reference import convolutional_encode,puncture_mask

TB='''library ieee;use ieee.std_logic_1164.all;use ieee.numeric_std.all;
use ieee.std_logic_textio.all;use std.textio.all;use std.env.all;
entity tb is end;architecture sim of tb is
signal clk:std_logic:='0';signal rst,in_valid,in_start,in_last:std_logic:='0';
signal pair:std_logic_vector(3 downto 0):=(others=>'0');
signal out_valid,out_bit,out_last:std_logic;
begin clk<=not clk after 2500 ps;
d:entity work.dut port map(clk=>clk,rst=>rst,in_valid=>in_valid,in_start=>in_start,
in_last=>in_last,pair=>pair,out_valid=>out_valid,out_bit=>out_bit,out_last=>out_last);
process file f:text open read_mode is "stimulus.txt";file o:text open write_mode is "trace.txt";
variable l,z:line;variable r,v,s,e,p:integer;variable cycle:integer:=0;
begin while not endfile(f) loop
readline(f,l);read(l,r);read(l,v);read(l,s);read(l,e);read(l,p);
wait until falling_edge(clk);rst<=std_logic'val(r+2);in_valid<=std_logic'val(v+2);
in_start<=std_logic'val(s+2);in_last<=std_logic'val(e+2);pair<=std_logic_vector(to_unsigned(p,4));
wait until rising_edge(clk);wait for 1 ns;
if r=1 then assert out_valid='0' report "reset failed" severity failure;
else assert out_valid='0' or out_valid='1' report "unknown valid" severity failure;
if out_valid='1' then write(z,cycle);write(z,string'(" "));write(z,out_bit);
write(z,string'(" "));write(z,out_last);writeline(o,z);end if;end if;
cycle:=cycle+1;end loop;report "HARNESS_DONE";finish;end process;end;'''

DRIVER=r'''#include "Vdut.h"
#include "verilated.h"
#include <fstream>
#include <iostream>
int main(int argc,char**argv){Verilated::commandArgs(argc,argv);Vdut d;
std::ifstream f("stimulus.txt");std::ofstream o("mapped-trace.txt");
int r,v,s,e,p;unsigned long cycle=0;
while(f>>r>>v>>s>>e>>p){d.clk=0;d.rst=r;d.in_valid=v;d.in_start=s;d.in_last=e;d.pair=p;d.eval();
d.clk=1;d.eval();if(r&&d.out_valid){std::cerr<<"reset failed";return 2;}
if(!r&&d.out_valid)o<<cycle<<" "<<int(d.out_bit)<<" "<<int(d.out_last)<<"\n";
++cycle;}d.final();return 0;}'''


def fixtures(folder: Path, *, audit: bool = False) -> list[list[int]]:
    rng=np.random.default_rng(654738 if audit else 88491)
    expected=[];lines=['1 0 0 0 0']*4
    lengths=[1,2,23,24,35,63,64,65,95,127,128,129,216,287,4096]+([32782] if audit else [])
    for length in lengths:
        for rate in (6,48,54):
            bits=rng.integers(0,2,length,dtype=np.uint8)
            encoded=convolutional_encode(bits)
            mask=np.resize(puncture_mask(rate),len(encoded)).astype(bool)
            obs=np.where(mask,encoded,2).reshape(-1,2)
            # For non-terminated short punctured sequences multiple final paths can tie.
            # Unpunctured short cases and punctured terminated codewords are unambiguous.
            if rate!=6:
                if length<24:continue
                bits[-6:]=0;encoded=convolutional_encode(bits);obs=np.where(mask,encoded,2).reshape(-1,2)
            expected.append([int(b) for b in bits])
            for i,(a,b) in enumerate(obs):
                lines.append(f'0 1 {int(i==0)} {int(i==len(obs)-1)} {int(a)+4*int(b)}')
                lines += ['0 0 0 0 0']*(2+int(i%17==0))
            lines += ['0 0 0 0 0']*260
    (folder/'stimulus.txt').write_text('\n'.join(lines)+'\n')
    write_json(folder/'expected.json',expected)
    return expected


def evaluate(source: str,folder: Path, *, audit: bool = False) -> dict:
    folder.mkdir(parents=True,exist_ok=True)
    result={'accepted':False,'vhdl_sha256':digest(source.encode()),'audit':audit}
    try:
        from .wifi_hardware import validate
        validate(source)
        (folder/'candidate.vhd').write_text(source);(folder/'tb.vhd').write_text(TB)
        expected=fixtures(folder,audit=audit)
        tools=Toolchain.configured()
        tools.run(['sh','-ec','ghdl -a --std=08 candidate.vhd tb.vhd\nghdl -e --std=08 tb\n'
                   'ghdl -r --std=08 tb --assert-level=error'],folder,timeout=300,log='simulation.log')
        actual=[];pending=[]
        for row in (folder/'trace.txt').read_text().splitlines():
            cycle,bit,last=row.split()
            if bit not in ('0','1') or last not in ('0','1'):raise ValueError('Unknown output bits')
            pending.append(int(bit))
            if last=='1':actual.append(pending);pending=[]
        if pending:raise ValueError('Output missing last')
        if len(actual)!=len(expected):raise ValueError(f'Codeword count {len(actual)} != {len(expected)}')
        failures=[]
        for i,(a,b) in enumerate(zip(actual,expected)):
            if a!=b:
                failures.append({'job':i,'expected_length':len(b),'actual_length':len(a),
                                 'first_mismatches':[j for j,(x,y) in enumerate(zip(a,b)) if x!=y][:12]})
        result.update(codewords=len(expected),decoded_bits=sum(map(len,expected)),failures=failures[:8],accepted=not failures)
    except Exception as exc:result['error']=str(exc)[-8000:]
    write_json(folder/'result.json',result)
    return result


def repair_loop(root: Path,rounds: int = 5,model: str | None = None):
    """Resume the initial generated candidate; feed only development feedback back."""
    from .providers import CodexProvider,SCHEMA
    provider=CodexProvider(model,timeout=1800)
    original=(root/'round-01/prompt.txt').read_text()
    history=[]
    for iteration in range(rounds):
        folder=root/f'round-{iteration+1:02d}'
        proposal=json.loads((folder/'response.json').read_text())
        result=evaluate(proposal['vhdl'],folder)
        history.append(result);write_json(root/'history.json',history)
        print(iteration+1,result,flush=True)
        if result['accepted']:
            final=evaluate(proposal['vhdl'],root/'final',audit=True)
            write_json(root/'final/proposal.json',proposal)
            write_json(root/'summary.json',{'accepted':final['accepted'],'development':result,'audit':final,'rounds_used':len(history)})
            return final
        if iteration+1<rounds:
            prompt=original+'\nPREVIOUS CANDIDATE:\n'+proposal['vhdl']+'\nTRUSTED FEEDBACK:\n'+json.dumps(result)
            next_folder=root/f'round-{iteration+2:02d}'
            if not (next_folder/'response.json').exists():
                provider.request(prompt,next_folder,SCHEMA)
    return result


def verify_mapped(folder: Path) -> dict:
    """Compile a synthesized FEC netlist and replay the same held-out stream."""
    (folder/'driver.cpp').write_text(DRIVER)
    tools=Toolchain.configured()
    tools.run(['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','dut',
               '--Mdir','obj_dir','-o','fec_sim','mapped.v','driver.cpp','/usr/share/yosys/xilinx/cells_sim.v'],
              folder,timeout=900,log='mapped-build.log')
    tools.run(['./obj_dir/fec_sim'],folder,timeout=300,log='mapped-simulation.log')
    expected=json.loads((folder/'expected.json').read_text())
    actual=[];pending=[]
    for row in (folder/'mapped-trace.txt').read_text().splitlines():
        _,bit,last=row.split();pending.append(int(bit))
        if last=='1':actual.append(pending);pending=[]
    result={'pass':actual==expected and not pending,'codewords':len(actual),
            'bits':sum(map(len,actual)),'simulator':'Verilator, Xilinx-mapped netlist',
            'mapped_sha256':digest((folder/'mapped.v').read_bytes())}
    write_json(folder/'mapped-result.json',result)
    return result
