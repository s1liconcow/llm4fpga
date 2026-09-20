"""Reproducible local hardware translation experiments."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from .benchmarks import prepare_normalizer, prepare_sha256
from .contracts import TranslationSpec, load_vectors
from .emit_vhdl import normalizer_vhdl
from .hash_demo import hash_message
from .problem import Contract, Golden, ROOT, write_json
from .structured import Design, search
from .slam_kernel import prepare_slam
from .toolchain import Toolchain
from .translate import translate
from .vhdl import Proposal, evaluate


def doctor() -> int:
    report = {'codex': shutil.which('codex'), 'podman': shutil.which('podman')}
    try:
        tools = Toolchain.configured()
        versions = tools.run(['sh','-ec','ghdl --version\nyosys -V\niverilog -V\nverilator --version\noctave --version'],
                                    ROOT / 'runs/doctor', timeout=30, log='versions.log')
        report['tools'] = [line for line in versions.splitlines() if not line.startswith('GHDL is') and line.startswith(
            ('GHDL ', 'Yosys ', 'Icarus Verilog version', 'Verilator ', 'GNU Octave, version'))]
        report['ready'] = bool(report['codex'])
    except Exception as exc:
        report['ready'] = False
        report['error'] = str(exc)
    print(json.dumps(report, indent=2))
    return 0 if report['ready'] else 2


def octave_golden(out: Path) -> Golden:
    out.mkdir(parents=True, exist_ok=True)
    for name in ('normalize_sensor.m','export_golden.m'):
        shutil.copy2(ROOT / 'examples/normalizer' / name, out / name)
    Toolchain.configured().run(['octave','--quiet','--no-gui','--eval',"export_golden('golden.csv');"],
                                out, timeout=60,log='octave.log')
    write_json(out / 'golden.meta.json',{'engine':'gnu-octave','matlab_executed':False,'octave_executed':True})
    return Golden.load(out / 'golden.csv')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Python/MATLAB -> Codex -> verified VHDL, using Podman')
    sub = parser.add_subparsers(dest='command',required=True)
    sub.add_parser('doctor',help='Check Codex and container hardware tools')
    from .wifi import add_parser as add_wifi_parser
    add_wifi_parser(sub)
    hash_cmd = sub.add_parser('hash',help='Hash text or a file through the generated VHDL core in simulation')
    data = hash_cmd.add_mutually_exclusive_group(required=True)
    data.add_argument('--text')
    data.add_argument('--file',type=Path)
    hash_cmd.add_argument('--proposal',type=Path,default=ROOT/'examples/sha256/recorded_codex.json')
    hash_cmd.add_argument('--out',type=Path,default=Path('runs/hash'))
    prepare = sub.add_parser('prepare',help='Create source, contract and independent vectors')
    prepare.add_argument('benchmark',choices=['sha256','normalizer','slam'])
    prepare.add_argument('--out',type=Path,required=True)
    prepare.add_argument('--octave',action='store_true')
    from .search import SearchConfig, read_seed, run_search, translation_problem
    search_cmd = sub.add_parser('search',help='Search beyond the first passing VHDL design using measured resources')
    search_cmd.add_argument('spec',type=Path)
    search_cmd.add_argument('--out',type=Path,required=True)
    search_cmd.add_argument('--workers',type=int,default=2,help='Concurrent design workers (default: 2)')
    search_cmd.add_argument('--rounds',type=int,default=3,help='Proposals per worker, including improvements after passing')
    search_cmd.add_argument('--tool-jobs',type=int,default=1,help='Maximum concurrent hardware evaluations')
    search_cmd.add_argument('--objective',choices=['luts','ffs','dsps','brams','latency'],default='luts')
    search_cmd.add_argument('--seed',type=Path,help='Starting JSON proposal, freshly evaluated as the baseline')
    search_cmd.add_argument('--model')
    search_cmd.add_argument('--resume',action='store_true',help='Resume the same search; reuse unchanged completed evaluations')
    for name in ('translate','demo'):
        cmd = sub.add_parser(name,help='Translate arbitrary contracted source' if name=='translate' else 'Run a verified end-to-end benchmark')
        if name=='translate':
            cmd.add_argument('spec',type=Path)
        else:
            cmd.add_argument('benchmark',choices=['sha256','normalizer','slam'])
            cmd.add_argument('--octave',action='store_true')
        cmd.add_argument('--out',type=Path,required=True)
        cmd.add_argument('--rounds',type=int,default=3)
        cmd.add_argument('--model')
        cmd.add_argument('--replay',type=Path,help='Verify a saved JSON proposal without calling Codex')
        cmd.add_argument('--simulation-only',action='store_true')
        cmd.add_argument('--resume',action='store_true',help='Recheck saved candidates and continue an interrupted run')
    slam = sub.add_parser('slam',help='Run scan-to-map SLAM with generated, Xilinx-mapped RTL in the tracking loop')
    slam.add_argument('--candidate',type=Path,required=True,help='Verified final directory from demo slam')
    slam.add_argument('--out',type=Path,required=True)
    slam.add_argument('--frames',type=int,default=120)
    slam.add_argument('--seed',type=int,default=41)
    slam.add_argument('--server',type=Path,help='Reuse an already compiled matching simulator directory')
    slam.add_argument('--carmen',type=Path,help='Use recorded 180/181-beam CARMEN FLASER scans (.clf or .bz2)')
    slam.add_argument('--stride',type=int,default=10,help='Recorded-data scan stride')
    slam.add_argument('--start',type=int,default=0,help='First recorded scan index')
    structured = sub.add_parser('structured',help='Run the original numerical design search')
    structured.add_argument('--out',type=Path,default=Path('runs/structured'))
    structured.add_argument('--hardware',action='store_true')
    structured.add_argument('--octave',action='store_true')
    xls = sub.add_parser('xls',help='Compile DSLX with pinned upstream XLS in its x86 Linux container')
    xls.add_argument('source',type=Path)
    xls.add_argument('--out',type=Path,required=True)
    args = parser.parse_args(argv)
    try:
        if args.command=='wifi':
            from .wifi import run as run_wifi
            return run_wifi(args)
        if args.command=='doctor':
            return doctor()
        if args.command=='search':
            config=SearchConfig(args.workers,args.rounds,args.tool_jobs,args.objective)
            if args.seed and args.seed.suffix.lower() in ('.vhd','.vhdl'):
                raise ValueError('--seed requires a JSON proposal including its declared latency')
            result=run_search(translation_problem(args.spec),args.out,config=config,
                              seed=read_seed(args.seed),model=args.model,resume=args.resume)
            print(json.dumps({'accepted':result['accepted'],'selected':result.get('selected'),
                              'improvement':result.get('improvement'),'summary':str(args.out/'summary.json')},indent=2))
            return 0 if result['accepted'] else 2
        if args.command=='hash':
            message = args.text.encode('utf-8') if args.text is not None else args.file.read_bytes()
            print(json.dumps(hash_message(message,args.proposal,args.out),indent=2))
            return 0
        if args.command=='slam':
            from .slam import load_carmen, run_slam
            try:
                import scipy, matplotlib  # check optional dependencies before doing work
            except ImportError as exc:
                raise ValueError('Install the SLAM extras: uv pip install -e ".[slam]"') from exc
            dataset = load_carmen(args.carmen,frames=args.frames,stride=args.stride,start=args.start) if args.carmen else None
            result = run_slam(args.candidate,args.out,frames=args.frames,seed=args.seed,server=args.server,dataset=dataset)
            print(json.dumps({'accepted':result['accepted'],'metrics':result['metrics'],
                              'summary':str(args.out/'summary.json'),'plot':str(args.out/'trajectory.png')},indent=2))
            return 0 if result['accepted'] else 2
        if args.command in {'prepare','demo'}:
            if args.command == 'demo' and not args.resume and (args.out / 'manifest.json').exists():
                raise ValueError('Output already has results; choose a fresh --out directory')
            if args.command == 'demo' and args.resume and (args.out / 'manifest.json').exists():
                spec_path = args.out / 'input/spec.json'
                if TranslationSpec.load(spec_path).name != args.benchmark:
                    raise ValueError('Resume benchmark must match the original run')
            else:
                golden = octave_golden(args.out / 'oracle') if args.octave and args.benchmark=='normalizer' else None
                spec_path = (prepare_sha256(args.out / 'input') if args.benchmark=='sha256'
                             else prepare_slam(args.out / 'input') if args.benchmark=='slam'
                             else prepare_normalizer(args.out / 'input',golden))
            if args.command=='prepare':
                print(spec_path)
                return 0
        if args.command in {'translate','demo'}:
            summary = translate(args.spec if args.command=='translate' else spec_path, args.out,
                                rounds=args.rounds,model=args.model,replay=args.replay,
                                synthesize=not args.simulation_only,resume=args.resume)
            print(json.dumps({'accepted':summary['accepted'],'summary':str(args.out/'summary.json')},indent=2))
            return 0 if summary['accepted'] else 2
        if args.command=='structured':
            golden = octave_golden(args.out / 'oracle') if args.octave else Golden.load()
            rows = search(golden,Contract(),args.out)
            for row in rows:
                design = Design(**row['design'])
                (args.out/design.name/'candidate.vhd').write_text(normalizer_vhdl(design))
            if args.hardware:
                spec_path=prepare_normalizer(args.out/'input',golden)
                spec=TranslationSpec.load(spec_path)
                # Freeze the choice based on development search, then audit every code.
                design=Design(**rows[0]['design'])
                vectors=load_vectors(spec_path.parent/spec.vectors,spec)+load_vectors(spec_path.parent/spec.audit_vectors,spec)
                result=evaluate(Proposal(normalizer_vhdl(design),1,'structured search'),spec,vectors,
                                args.out/'verified',Toolchain.configured())
                print(json.dumps(result,indent=2))
                return 0 if result['accepted'] else 2
            print(json.dumps([r['name'] for r in rows],indent=2))
            return 0
        if args.command=='xls':
            args.out.mkdir(parents=True,exist_ok=True)
            shutil.copy2(args.source,args.out/'candidate.x')
            shutil.copy2(ROOT/'scripts/xls_to_verilog.sh',args.out/'xls_to_verilog.sh')
            tools=Toolchain('podman','xls-e2e-xls:local','linux/amd64')
            print(tools.run(['bash','xls_to_verilog.sh','candidate.x','.'],args.out,timeout=300,log='xls.log'))
            return 0
    except (ValueError, RuntimeError, FileNotFoundError, TimeoutError, subprocess.SubprocessError) as exc:
        parser.exit(2,f'error: {exc}\n')
    return 0
