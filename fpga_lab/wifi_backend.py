"""Feed independent floating-point equalizer outputs into the generated PHY backend."""
from __future__ import annotations
import json
from dataclasses import replace
from pathlib import Path
import re
import numpy as np

from .problem import write_json,digest
from .toolchain import Toolchain
from .wifi_reference import ACTIVE,DATA,LTS_SIGNS,PILOTS,POLARITY,RATES
from .wifi_fixtures import cases,Case
from .wifi_hardware import TESTBENCH,scoreboard,validate


def symbols(case: Case):
    for packet in case.expected:
        x=case.samples[packet.start:packet.end]
        x=x*np.exp(-1j*packet.cfo*np.arange(len(x)))
        gain=(np.fft.fft(x[192:256])+np.fft.fft(x[256:320]))/2
        gain[ACTIVE%64]*=LTS_SIGNS
        count=(packet.end-packet.start-320)//80
        for k in range(count):
            bins=np.fft.fft(x[336+k*80:400+k*80])
            phase=np.angle(np.sum(np.conj(bins[PILOTS%64])*gain[PILOTS%64]*POLARITY[k%127]*np.array([1,1,1,-1])))
            equal=bins[DATA%64]*np.exp(1j*phase)/gain[DATA%64]
            packed=0
            for i,z in enumerate(equal):
                a=int(np.clip(np.rint(z.real*4096),-32768,32767))&65535
                b=int(np.clip(np.rint(z.imag*4096),-32768,32767))&65535
                packed|=(a+(b<<16))<<(32*i)
            yield k==0,k==count-1,packed


def backend_tb() -> str:
    tb=TESTBENCH.replace('std_logic_vector(31 downto 0) :=','std_logic_vector(1535 downto 0) :=')
    tb=tb.replace('variable data : std_logic_vector(31 downto 0);','variable data : std_logic_vector(1537 downto 0);')
    tb=tb.replace('begin\n  clk <=', '''signal in_header,in_last,in_ready,header_valid,header_error : std_logic;
signal header_rate:std_logic_vector(7 downto 0);
signal header_length:std_logic_vector(11 downto 0);
begin
debug_valid<=header_valid or header_error;
debug_tag<=x"03" when header_valid='1' else x"05";
debug_data<=x"00" & header_rate & x"0" & header_length;
  clk <=''')
    names=['clk','rst','in_valid','in_header','in_last','out_ready','input_data','in_ready',
           'header_valid','header_error','header_rate','header_length','out_valid','out_first','out_last',
           'out_fcs_ok','overflow','output_data','out_rate','out_length']
    mapping=', '.join(f'{n}=>{n}' for n in names)
    tb=re.sub(r'd: entity work.dut port map\([\s\S]*?\);',f'd: entity work.dut port map({mapping});',tb)
    tb=tb.replace('input_data <= data;','input_data <= data(1535 downto 0); in_header<=data(1537); in_last<=data(1536);')
    tb=tb.replace("if r=0 then",'''if r=0 then
        assert v=0 or in_ready='1' report "Symbol processing exceeds 800-cycle deadline" severity failure;''')
    return tb


def evaluate(source: str,viterbi_source: str,folder: Path,data: Path, *, audit: bool = False) -> dict:
    folder.mkdir(parents=True,exist_ok=True)
    result={'accepted':False,'vhdl_sha256':digest(source.encode()),'viterbi_sha256':digest(viterbi_source.encode()),'audit':audit}
    try:
        validate(source)
        dependency=re.sub(r'\bdut\b','wifi_viterbi',viterbi_source,flags=re.I)
        (folder/'viterbi.vhd').write_text(dependency);(folder/'candidate.vhd').write_text(source)
        (folder/'tb.vhd').write_text(backend_tb())
        tools=Toolchain.configured()
        tools.run(['sh','-ec','ghdl -a --std=08 viterbi.vhd candidate.vhd tb.vhd\nghdl -e --std=08 tb'],folder,log='compile.log')
        rows=[]
        for case in cases(data,audit=audit):
            if case.reset_at is not None or not case.expected:continue
            cycle=0
            with (folder/'stimulus.txt').open('w') as f:
                def emit(r=0,v=0,value=0):
                    nonlocal cycle
                    ready=int(not case.stalls or cycle%257>=32)
                    f.write(f'{r} {v} {ready} {value:0385x}\n');cycle+=1
                for _ in range(5):emit(1)
                for header,last,word in symbols(case):
                    if header:
                        for _ in range(6000):emit()
                    emit(v=1,value=word|(int(header)<<1537)|(int(last)<<1536))
                    for _ in range(799):emit()
                for _ in range(10000):emit()
            tools.run(['ghdl','-r','--std=08','tb','--assert-level=error'],folder,timeout=600,log='simulation.log')
            # This stage inserts a header drain interval into the symbol stream.
            # Raw-IQ arrival-rate checks belong to the integrated receiver.
            scored=scoreboard((folder/'trace.txt').read_text(),replace(case,
                provenance={**case.provenance,'check_sustained_rate':False}))
            scored['name']=case.name;scored['debug_tail']=(folder/'debug.txt').read_text().splitlines()[-10:]
            rows.append(scored);write_json(folder/f'{case.name}.json',scored)
            print(case.name,scored['pass'],flush=True)
            if not scored['pass']:break
        result.update(accepted=all(r['pass'] for r in rows) and bool(rows),results=rows)
        if result['accepted']:
            # Reject unsupported constructs at the component boundary, before
            # the frontend relies on this implementation.
            tools.run(['sh','-ec',
                'ghdl --synth --std=08 --out=verilog viterbi.vhd candidate.vhd -e dut > /tmp/wifi-netlist.v\n'
                'cp /tmp/wifi-netlist.v netlist.v'],
                folder,timeout=300,log='synthesis.log')
            result['synthesized_verilog_sha256']=digest((folder/'netlist.v').read_bytes())
    except Exception as exc:
        result['accepted']=False
        result['error']=str(exc)[-8000:]
    write_json(folder/'result.json',result)
    return result


def repair_loop(root: Path, viterbi_source: str,data: Path,rounds: int = 6,model: str | None = None):
    from .providers import CodexProvider,SCHEMA
    provider=CodexProvider(model,timeout=1800);original=(root/'round-01/prompt.txt').read_text();history=[]
    for iteration in range(rounds):
        folder=root/f'round-{iteration+1:02d}'
        proposal=json.loads((folder/'response.json').read_text())
        result=evaluate(proposal['vhdl'],viterbi_source,folder,data)
        history.append(result);write_json(root/'history.json',history)
        print('round',iteration+1,json.dumps(result)[:5000],flush=True)
        if result['accepted']:
            final=evaluate(proposal['vhdl'],viterbi_source,root/'final',data,audit=True)
            write_json(root/'final/proposal.json',proposal)
            write_json(root/'summary.json',{'accepted':final['accepted'],'development':result,'audit':final,'rounds_used':len(history)})
            return final
        if iteration+1<rounds:
            next_folder=root/f'round-{iteration+2:02d}'
            if not (next_folder/'response.json').exists():
                provider.request(original+'\nPREVIOUS CANDIDATE:\n'+proposal['vhdl']+'\nTRUSTED FEEDBACK:\n'+json.dumps(result),
                                 next_folder,SCHEMA)
    return result
