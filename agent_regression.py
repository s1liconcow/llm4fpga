"""Multi-worker model-driven RTL regression.

Pattern: worker proposes complete RTL -> trusted harness evaluates it -> numerical /
synthesis feedback is returned -> worker revises.  The final 4096-input audit is
withheld from workers.  Inspired by Poetiq's parallel coding-expert loop, but the
scorer and golden model are immutable.

Live mode expects an OpenAI-compatible Responses API. Set ASTRA_MODEL and,
optionally, ASTRA_BASE_URL. Replay mode is deterministic and requires no model.
"""
from __future__ import annotations
import argparse, asyncio, json, math, os, re, shutil, subprocess, tempfile
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
    if "$" in clean or "`" in clean or '"' in clean: raise ValueError("system/preprocessor/string hooks forbidden")

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

def run(cmd,cwd,timeout=120):
    p=subprocess.run(cmd,cwd=cwd,text=True,capture_output=True,timeout=timeout)
    if p.returncode: raise RuntimeError((p.stdout+"\n"+p.stderr)[-4000:])
    return p.stdout

def tb(codes,latency):
    # Fixed trusted scoreboard protocol. Each valid input must emerge exactly latency clocks later.
    stim="\n".join(f"{c & 4095:03x}" for c in codes)+"\n"
    source=f"""module tb;
reg clk=0,rst=1,in_valid=0; reg signed [11:0] x=0;
wire out_valid; wire signed [15:0] y; integer i;
reg [11:0] mem[0:{len(codes)-1}];
dut d(.clk(clk),.rst(rst),.in_valid(in_valid),.x(x),.out_valid(out_valid),.y(y));
always #5 clk=~clk;
initial begin
 $readmemh("stim.hex",mem); repeat(3) @(posedge clk); rst=0;
 for(i=0;i<{len(codes)};i=i+1) begin @(negedge clk); in_valid=1; x=mem[i]; @(posedge clk); #1; if(out_valid) $display("O %h",y); end
 @(negedge clk); in_valid=0; repeat({latency+3}) begin @(posedge clk); #1; if(out_valid) $display("O %h",y); end
 $finish; end endmodule"""
    return stim,source

def evaluate(p,folder,codes,physical=True):
    folder.mkdir(parents=True,exist_ok=True)
    result={"accepted":False,"latency":p.latency}
    try:
        validate_rtl(p.rtl)
        if not 1 <= p.latency <= 16: raise ValueError("latency outside contract")
        (folder/"candidate.v").write_text(p.rtl)
        stim,test=tb(codes,p.latency); (folder/"stim.hex").write_text(stim); (folder/"tb.v").write_text(test)
        run(["iverilog","-g2012","-s","tb","-o","sim","candidate.v","tb.v"],folder)
        text=run(["vvp","sim"],folder)
        vals=[]
        for line in text.splitlines():
            if line.startswith("O "):
                h=line.split()[1]
                if not re.fullmatch(r"[0-9a-fA-F]{4}",h): raise RuntimeError("unknown output")
                v=int(h,16); vals.append(v-65536 if v>=32768 else v)
        if len(vals)!=len(codes): raise RuntimeError(f"protocol/latency mismatch: {len(vals)} outputs for {len(codes)} inputs")
        actual=np.array(vals)/16384.0; ref=golden(codes); err=np.abs(actual-ref)
        result["numeric"]={"max_abs_error":float(err.max()),"rms_error":float(np.sqrt(np.mean(err*err))),"violations":int((err>2e-4).sum())}
        result["numeric"]["pass"]=result["numeric"]["max_abs_error"]<=2e-4 and result["numeric"]["rms_error"]<=6e-5
        if result["numeric"]["pass"] and physical:
            run(["yosys","-p","read_verilog candidate.v; synth_ice40 -top dut -json net.json; check -assert"],folder)
            net=json.loads((folder/"net.json").read_text()); cells=net["modules"]["dut"]["cells"]
            counts={}
            for c in cells.values(): counts[c["type"]]=counts.get(c["type"],0)+1
            result["hardware"]={"luts":counts.get("SB_LUT4",0),"ffs":sum(v for k,v in counts.items() if k.startswith("SB_DFF")),"cells":counts}
            result["accepted"]=True
    except Exception as e: result["error"]=str(e)[-4000:]
    (folder/"result.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    return result

async def worker(i,rounds,provider,root,dev,sem):
    history=""; records=[]
    for it in range(rounds):
        prompt=f"Worker {i}, iteration {it}. Previous trusted feedback:\n{history[-8000:] or 'none'}"
        try: p=await provider.propose(prompt)
        except Exception as e: records.append({"iteration":it,"provider_error":str(e)}); continue
        async with sem: r=await asyncio.to_thread(evaluate,p,root/f"w{i}-i{it}",dev,True)
        records.append({"iteration":it,"proposal_notes":p.notes,"result":r,"proposal":p})
        history=json.dumps(r,sort_keys=True)
    return records

def rank(x):
    r=x["result"]; n=r.get("numeric",{})
    return (0 if r.get("accepted") else 1,n.get("violations",10**9),r.get("hardware",{}).get("luts",10**9),n.get("max_abs_error",1e9))

async def main_async(args):
    if not shutil.which("iverilog") or not shutil.which("yosys"): raise RuntimeError("Install iverilog and yosys")
    rng=np.random.default_rng(20260919); all_codes=np.arange(-2048,2048); rng.shuffle(all_codes)
    dev=np.sort(all_codes[:2048]); audit=np.sort(all_codes[2048:])
    provider=Astra(); root=Path(args.out); root.mkdir(parents=True,exist_ok=True)
    groups=await asyncio.gather(*[worker(i,args.rounds,provider,root,dev,asyncio.Semaphore(args.tool_jobs)) for i in range(args.workers)])
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
    a=ap.parse_args()
    if not 1<=a.workers<=32 or not 1<=a.rounds<=50: raise SystemExit("workers/rounds outside bounds")
    return asyncio.run(main_async(a))
if __name__=="__main__": raise SystemExit(main())
