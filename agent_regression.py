"""Multi-worker model-driven RTL regression.

Pattern: worker proposes complete RTL -> trusted harness evaluates it -> numerical /
synthesis feedback is returned -> worker revises.  The final 4096-input audit is
withheld from workers.  Inspired by Poetiq's parallel coding-expert loop, but the
scorer and golden model are immutable.

Legacy live mode expects an OpenAI-compatible Responses API. The main VHDL CLI uses Codex directly. Set ASTRA_MODEL and,
optionally, ASTRA_BASE_URL. Replay mode is deterministic and requires no model.
"""
from __future__ import annotations
import argparse, asyncio, json, os, re, shutil
from dataclasses import dataclass
from pathlib import Path
import numpy as np

SYSTEM = """Return ONLY JSON: {"rtl":"<complete Verilog>","latency":N,"notes":"..."}.
Build module dut(clk,rst,in_valid,x,out_valid,y). x is signed 12-bit representing
x/2048. y is signed Q1.14 approximating x/sqrt(0.25+x*x). Synchronous active-high
reset must flush validity. Initiation interval is one. Max latency 16. No real
arithmetic, initial/final blocks, files, DPI, system tasks, preprocessor, strings
or testbench references. Optimize for low LUT count after meeting max absolute
error 2e-4 and RMS error 6e-5."""

def golden(x):
    z=np.asarray(x,dtype=np.float64)/2048.0
    return z/np.sqrt(0.25+z*z)

def validate_rtl(s):
    if not isinstance(s,str) or not 20 < len(s) < 200000: raise ValueError("bad RTL size")
    clean=re.sub(r"/\*.*?\*/|//[^\n]*","",s,flags=re.S)
    if not re.search(r"\bmodule\s+dut\b",clean): raise ValueError("top must be dut")
    if re.search(r"\b(initial|final|real|realtime|shortreal|import|export|bind)\b",clean): raise ValueError("forbidden RTL construct")
    safe_casts=re.sub(r"\$(signed|unsigned|clog2)\b", "", clean)
    if "$" in safe_casts or "`" in clean or '"' in clean: raise ValueError("system/preprocessor/string hooks forbidden")

@dataclass
class Proposal:
    rtl:str
    latency:int
    notes:str=""

def parse(text):
    m=re.search(r"\{.*\}",text,re.S)
    if not m: raise ValueError("no JSON")
    o=json.loads(m.group())
    if not isinstance(o.get("rtl"),str) or type(o.get("latency")) is not int: raise ValueError("bad proposal")
    return Proposal(o["rtl"],o["latency"],str(o.get("notes",""))[:1000])

class Astra:
    def __init__(self):
        from openai import AsyncOpenAI
        kw={}
        if os.getenv("ASTRA_BASE_URL"): kw["base_url"]=os.environ["ASTRA_BASE_URL"]
        self.client=AsyncOpenAI(**kw)
        self.model=os.getenv("ASTRA_MODEL")
        if not self.model: raise RuntimeError("Set ASTRA_MODEL to your Astra deployment/model id")
    async def propose(self,prompt):
        r=await self.client.responses.create(model=self.model,instructions=SYSTEM,input=prompt,max_output_tokens=12000)
        return parse(r.output_text)

class Replay:
    async def propose(self,prompt):
        from fpga_lab.structured import Design
        return Proposal(Design().verilog_fixture(),2,"Deterministic fixture, not model output")

def evaluate(p,folder,codes,physical=True):
    from fpga_lab.legacy import evaluate_verilog
    return evaluate_verilog(p,folder,codes,physical)

async def worker(i,rounds,provider,root,dev,sem):
    history=""; records=[]
    for it in range(rounds):
        prompt=f"Worker {i}, iteration {it}. Previous trusted feedback:\n{history[-8000:] or 'none'}"
        try: p=await provider.propose(prompt)
        except Exception as e: records.append({"iteration":it,"provider_error":str(e)}); continue
        async with sem: r=await asyncio.to_thread(evaluate,p,root/f"w{i}-i{it}",dev,True)
        records.append({"iteration":it,"proposal_notes":p.notes,"result":r,"proposal":p})
        history=json.dumps({"previous_rtl":p.rtl,"trusted_feedback":r},sort_keys=True)
    return records

def rank(x):
    r=x["result"]; n=r.get("numeric",{})
    return (0 if r.get("accepted") else 1,n.get("violations",10**9),r.get("hardware",{}).get("luts",10**9),n.get("max_abs_error",1e9))

async def main_async(args):
    if not shutil.which("podman"): raise RuntimeError("Install Podman and build the tools image")
    rng=np.random.default_rng(20260919); all_codes=np.arange(-2048,2048); rng.shuffle(all_codes)
    dev=np.sort(all_codes[:2048]); audit=np.sort(all_codes[2048:])
    provider=Replay() if args.provider == "replay" else Astra()
    root=Path(args.out); root.mkdir(parents=True,exist_ok=True)
    semaphore=asyncio.Semaphore(args.tool_jobs)
    groups=await asyncio.gather(*[worker(i,args.rounds,provider,root,dev,semaphore) for i in range(args.workers)])
    candidates=[r for g in groups for r in g if "result" in r]
    if not candidates: raise RuntimeError("no evaluable proposals")
    best=min(candidates,key=rank); p=best["proposal"]
    # Hidden audit: union with dev gives exhaustive 4096-code final verification.
    final=evaluate(p,root/"FINAL_AUDIT",np.arange(-2048,2048),True)
    summary={"selected_dev":rank(best),"final_audit":final,"workers":args.workers,"rounds":args.rounds}
    (root/"summary.json").write_text(json.dumps(summary,indent=2,sort_keys=True)+"\n")
    return 0 if final.get("accepted") else 2

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--workers",type=int,default=8); ap.add_argument("--rounds",type=int,default=10); ap.add_argument("--tool-jobs",type=int,default=2); ap.add_argument("--out",default="runs/astra")
    ap.add_argument("--provider",choices=["replay","astra"],default="astra")
    a=ap.parse_args()
    if not 1<=a.workers<=32 or not 1<=a.rounds<=50 or not 1<=a.tool_jobs<=8: raise SystemExit("workers/rounds outside bounds")
    return asyncio.run(main_async(a))
if __name__=="__main__": raise SystemExit(main())
