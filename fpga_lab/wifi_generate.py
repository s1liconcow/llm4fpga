"""Generate bounded receiver stages with Codex and trusted feedback."""
from __future__ import annotations
import ast
import json
from pathlib import Path
import re

from .problem import ROOT,write_json,digest
from .providers import CodexProvider,SCHEMA


def algorithm_source() -> str:
    source=(ROOT/'fpga_lab/wifi_reference.py').read_text()
    tree=ast.parse(source)
    return '\n\n'.join(ast.get_source_segment(source,n) for n in tree.body
        if not isinstance(n,(ast.FunctionDef,ast.ClassDef)) or n.name not in ('transmit','load_iq'))


def generate(stage: str,out: Path, *, model: str | None = None) -> dict:
    template=(ROOT/f'examples/wifi/prompts/{stage}.txt').read_text()
    source=algorithm_source()
    prompt=template+source
    schema=SCHEMA
    if stage=='architecture':
        keys=['name','purpose','inputs','outputs','streaming_transformation','numerical_refinement','verification']
        schema={'type':'object','additionalProperties':False,'properties':{
            'architecture':{'type':'string'},'schedule':{'type':'string'},'risks':{'type':'string'},
            'stages':{'type':'array','items':{'type':'object','additionalProperties':False,
                'properties':{k:{'type':'string'} for k in keys},'required':keys}}},
            'required':['architecture','schedule','risks','stages']}
    result=CodexProvider(model,timeout=3600).request(prompt,out,schema)
    write_json(out/'generation.json',{'stage':stage,'source_sha256':digest(source.encode()),
                                     'prompt_sha256':digest(prompt.encode())})
    if stage!='architecture':(out/'candidate.vhd').write_text(result['vhdl'])
    return result


def combined_receiver(frontend: str,blocks: Path) -> str:
    parts=[]
    for folder,name in [('viterbi','wifi_viterbi'),('fft','wifi_fft64'),('backend','wifi_backend')]:
        final=blocks/folder/'final'
        proposal=json.loads((final/'proposal.json').read_text())
        result=json.loads((final/'result.json').read_text())
        if not result['accepted'] or result['vhdl_sha256']!=digest(proposal['vhdl'].encode()):
            raise ValueError(f'Unverified or changed dependency: {folder}')
        parts.append(re.sub(r'\bdut\b',name,proposal['vhdl'],flags=re.I))
    return '\n\n'.join([*parts,frontend])


def receiver_loop(out: Path,blocks: Path,data: Path, *, rounds: int = 6,model: str | None = None) -> dict:
    from .wifi_hardware import evaluate
    from .wifi_fixtures import cases
    prompt=(out/'round-01/prompt.txt').read_text()
    history=[];provider=CodexProvider(model,timeout=3600)
    development=cases(data)
    for iteration in range(rounds):
        folder=out/f'round-{iteration+1:02d}'
        proposal=json.loads((folder/'response.json').read_text())
        combined=combined_receiver(proposal['vhdl'],blocks)
        result=evaluate(combined,folder,development)
        history.append(result);write_json(out/'history.json',history)
        print('Receiver round',iteration+1,'accepted',result['accepted'],result.get('error',''),flush=True)
        if result['accepted']:
            audit=evaluate(combined,out/'final',cases(data,audit=True))
            write_json(out/'final/proposal.json',dict(proposal,vhdl=combined))
            summary={'accepted':audit['accepted'],'development':result,'audit':audit,'rounds_used':len(history)}
            write_json(out/'summary.json',summary);return summary
        if iteration+1<rounds:
            next_folder=out/f'round-{iteration+2:02d}'
            if not (next_folder/'response.json').exists():
                provider.request(prompt+'\nPREVIOUS FRONTEND:\n'+proposal['vhdl']+'\nTRUSTED DEVELOPMENT FEEDBACK:\n'+json.dumps(result),
                    next_folder,SCHEMA)
    summary={'accepted':False,'development':result,'audit':None,'rounds_used':len(history)}
    write_json(out/'summary.json',summary);return summary
