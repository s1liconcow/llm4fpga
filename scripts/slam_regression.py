#!/usr/bin/env python3
"""Run the fixed SLAM integration suite against one frozen verified candidate."""
import argparse
import json
from pathlib import Path

from fpga_lab.cosim import build_server
from fpga_lab.problem import write_json
from fpga_lab.slam import load_carmen, run_slam, synthetic_dataset
from fpga_lab.slam_kernel import specification


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate',type=Path,required=True)
    parser.add_argument('--carmen',type=Path,required=True,help='Pinned Intel .clf.bz2 from fetch_slam_data.py')
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    if (args.out/'summary.json').exists():
        parser.error('Choose a fresh output directory')
    cases = [
        ('synthetic-41',synthetic_dataset(frames=120,seed=41)),
        ('synthetic-9827',synthetic_dataset(frames=160,seed=9827)),
        ('intel-development',load_carmen(args.carmen,frames=240,stride=10)),
        ('intel-unseen-segment',load_carmen(args.carmen,frames=160,stride=10,start=4000)),
    ]
    args.out.mkdir(parents=True,exist_ok=True)
    report = {'accepted':False,'status':'in_progress','cases':{}}
    write_json(args.out/'summary.json',report)
    server = build_server(args.candidate,specification(),args.out/'cosim')
    report['simulator'] = json.loads((server/'server.json').read_text())
    report['frames'] = sum(len(dataset.scans) for _,dataset in cases)
    report['physical_fpga_executed'] = False
    for name,dataset in cases:
        print(f'\nSLAM integration case: {name}',flush=True)
        result = run_slam(args.candidate,args.out/name,dataset=dataset,server=server)
        report['cases'][name] = {key:result[key] for key in ['accepted','checks','metrics','dataset','kernel']}
        write_json(args.out/'summary.json',report)
    report['accepted'] = all(case['accepted'] for case in report['cases'].values())
    report['status'] = 'passed' if report['accepted'] else 'failed'
    report['kernel_packets'] = sum(case['kernel']['packets'] for case in report['cases'].values())
    write_json(args.out/'summary.json',report)
    print(json.dumps({'accepted':report['accepted'],'kernel_packets':report['kernel_packets'],
                      'report':str(args.out/'summary.json')},indent=2))
    return 0 if report['accepted'] else 2


if __name__=='__main__':
    raise SystemExit(main())
