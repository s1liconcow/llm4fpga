"""Containerized evaluator for the original Verilog normalizer experiment."""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from .contracts import TranslationSpec, Vector
from .problem import Golden, write_json
from .toolchain import Toolchain
from .vhdl import gate_testbench, score_trace, stimulus


def evaluate_verilog(proposal, folder: Path, codes, physical=True):
    # Imported lazily to preserve the original public entry point.
    from agent_regression import validate_rtl
    folder.mkdir(parents=True, exist_ok=True)
    result={'accepted':False,'latency':proposal.latency}
    try:
        validate_rtl(proposal.rtl)
        if not 1 <= proposal.latency <= 16:
            raise ValueError('Latency outside contract')
        spec=TranslationSpec('normalizer',12,16,16,1,'normalizer','normalize_sensor.m','dev','audit',
                             output_signed=True,output_scale=16384,max_abs_error=2e-4,max_rms_error=6e-5)
        golden=Golden.load()
        values=golden.at(np.asarray(codes))
        vectors=[Vector(int(c)&4095,int(np.rint(v*16384))&65535,float(v)) for c,v in zip(codes,values)]
        cycles,expected=stimulus(spec,vectors,proposal.latency)
        from .vhdl import testbench
        stim,_=testbench(spec,cycles)
        tb=gate_testbench(spec,len(cycles)).replace('.input_data(input_data)','.x(input_data)').replace('.output_data(output_data)','.y(output_data)')
        (folder/'candidate.v').write_text(proposal.rtl)
        (folder/'stimulus.txt').write_text(stim)
        (folder/'tb.v').write_text(tb)
        (folder/'gate_trace.txt').unlink(missing_ok=True)
        tools=Toolchain.configured()
        tools.run(['sh','-ec','iverilog -g2012 -s tb -o sim candidate.v tb.v\nvvp sim'],folder,log='simulation.log')
        result['numeric']=score_trace((folder/'gate_trace.txt').read_text(),spec,expected,len(cycles))
        if result['numeric']['pass']:
            if physical:
                tools.run(['yosys','-Q','-T','-p','read_verilog candidate.v; synth_xilinx -family xc7 -top dut -noiopad; check -assert; write_json net.json'],folder,timeout=300,log='synthesis.log')
                cells=json.loads((folder/'net.json').read_text())['modules']['dut']['cells']
                counts={}
                for cell in cells.values():
                    counts[cell['type']]=counts.get(cell['type'],0)+1
                result['hardware']={'luts':sum(v for k,v in counts.items() if re.fullmatch(r'LUT[1-6]',k)),
                                    'ffs':sum(v for k,v in counts.items() if k.startswith('FD')),
                                    'cells':counts,'target':'xilinx-7series','physical_timing_measured':False}
            result['accepted']=True
    except Exception as exc:
        result['error']=str(exc)[-8000:]
    write_json(folder/'result.json',result)
    return result
