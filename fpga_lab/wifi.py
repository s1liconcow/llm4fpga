"""CLI entry points for the Wi-Fi translation experiment."""
from __future__ import annotations
import json
import os
from pathlib import Path

from .problem import write_json
from .wifi_fixtures import cases,fetch,reference_report


def add_parser(sub):
    p=sub.add_parser('wifi',help='Verify and translate the streaming 802.11a/g receiver')
    p.add_argument('action',choices=['fetch','reference','verify','search','fft','plan','viterbi','backend','receiver'])
    p.add_argument('--data',type=Path,default=Path('runs/wifi-data/openofdm'))
    p.add_argument('--out',type=Path,default=Path('runs/wifi'))
    p.add_argument('--candidate',type=Path,help='Generated receiver VHDL file')
    p.add_argument('--blocks',type=Path,help='Directory containing verified fft, viterbi, backend stages')
    p.add_argument('--audit',action='store_true',help='Evaluate frozen candidate on held-out data')
    p.add_argument('--rounds',type=int,default=5)
    p.add_argument('--workers',type=int,default=2,help='Concurrent design workers for search')
    p.add_argument('--tool-jobs',type=int,default=1,help='Maximum concurrent hardware evaluations for search')
    p.add_argument('--objective',choices=['luts','ffs','dsps','brams'],default='luts',help='Measured search objective; brams uses BRAM18 equivalents')
    p.add_argument('--model')
    p.add_argument('--replay',type=Path,help='Replay a recorded FFT proposal (fft action only)')
    p.add_argument('--simulation-only',action='store_true',help='Skip mapping for verify or fft')
    p.add_argument('--target',choices=['compact','xc7a200t'],default='compact',
                   help='Resource limit profile for verify/search (default: original compact goal)')
    p.add_argument('--resume',action='store_true')


def run(args):
    os.environ.setdefault('FPGA_LAB_MEMORY','6144m')
    os.environ.setdefault('FPGA_LAB_IMAGE','xls-e2e-wifi:local')
    round_limit=50 if args.action=='search' else 20
    if not 1<=args.rounds<=round_limit:raise ValueError(f'--rounds must be in 1..{round_limit}')
    if args.replay is not None and args.action!='fft':
        raise ValueError('--replay is for fft; replay a receiver with verify --candidate')
    if args.simulation_only and args.action not in ('verify','fft'):
        raise ValueError('--simulation-only requires verify or fft')
    if args.audit and args.action!='verify':
        raise ValueError('--audit requires verify; generation audits the selected candidate automatically')
    if args.target!='compact' and args.action not in ('verify','search'):
        raise ValueError('--target applies to verify or search')
    if args.action=='search':
        from .search import SearchConfig, read_seed, run_search
        from .wifi_search import receiver_problem
        if args.candidate is None:
            raise ValueError('wifi search requires --candidate with a complete receiver VHDL or JSON proposal')
        config=SearchConfig(args.workers,args.rounds,args.tool_jobs,args.objective)
        result=run_search(receiver_problem(args.data,args.target),args.out,config=config,
                          seed=read_seed(args.candidate),model=args.model,resume=args.resume)
        print(json.dumps({'accepted':result['accepted'],'selected':result.get('selected'),
                          'improvement':result.get('improvement'),'summary':str(args.out/'summary.json')},indent=2))
        return 0 if result['accepted'] else 2
    if args.action=='fetch':
        report=fetch(args.data)
        print(json.dumps({'downloaded':len(report['files']),'data':str(args.data)}));return 0
    if args.action=='reference':
        report=reference_report(args.data,args.out)
        print(json.dumps({'passed':report['passed'],'captures':report['captures'],'packets':report['packets'],
                          'report':str(args.out/'reference.json')}));return 0
    if args.action in ('plan','viterbi','backend','receiver'):
        if args.action in ('backend','receiver') and args.blocks is None:
            raise ValueError('--blocks is required')
        from .wifi_generate import generate,receiver_loop
        stage={'plan':'architecture','receiver':'frontend'}.get(args.action,args.action)
        first=args.out/('architecture' if args.action=='plan' else 'round-01')
        if (first/'response.json').exists() and not args.resume:
            raise ValueError('Choose a fresh output directory or --resume')
        if not (first/'response.json').exists():generate(stage,first,model=args.model)
        if args.action=='plan':
            print(first/'response.json');return 0
        if args.action=='viterbi':
            from .wifi_viterbi import repair_loop
            result=repair_loop(args.out,rounds=args.rounds,model=args.model)
        else:
            if args.action=='backend':
                from .wifi_backend import repair_loop
                result=repair_loop(args.out,(args.blocks/'viterbi/final/candidate.vhd').read_text(),args.data,rounds=args.rounds,model=args.model)
            else:result=receiver_loop(args.out,args.blocks,args.data,rounds=args.rounds,model=args.model)
    elif args.action=='fft':
        from .wifi_blocks import prepare_fft
        from .translate import translate
        spec=prepare_fft(args.out/'input')
        result=translate(spec,args.out,rounds=args.rounds,model=args.model,replay=args.replay,
                         synthesize=not args.simulation_only,resume=args.resume)
    else:
        from .wifi_hardware import evaluate
        if args.candidate is None:raise ValueError('--candidate is required')
        if (args.out/'result.json').exists():raise ValueError('Choose a fresh output directory')
        fixtures=cases(args.data,audit=args.audit)
        args.out.mkdir(parents=True,exist_ok=True)
        write_json(args.out/'fixtures.json',[c.metadata() for c in fixtures])
        result=evaluate(args.candidate.read_text(),args.out,fixtures,synthesize=not args.simulation_only,target=args.target)
    print(json.dumps({'accepted':result['accepted'],'out':str(args.out)},indent=2))
    return 0 if result['accepted'] else 2
