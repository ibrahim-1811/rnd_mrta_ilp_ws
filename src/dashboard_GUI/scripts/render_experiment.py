#!/usr/bin/env python3
"""Create fresh readable plots/HTML without changing experiment artifacts."""
import argparse
from datetime import datetime,timezone
from pathlib import Path
import sys
import uuid
ROOT=Path(__file__).resolve().parents[3]
for package in ('dashboard_GUI/backend','mrta_core','simulation_env','task_management'):sys.path.insert(0,str(ROOT/'src'/package))
from control_center.adapters.experiments import ExperimentReader
from control_center.reports import render


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name',required=True,help='Exact existing experiment directory name')
    parser.add_argument('--scope',choices=['app','history'],default='app')
    args=parser.parse_args()
    if not args.name or Path(args.name).name!=args.name or args.name in ('.','..'):parser.error('Use an experiment name, not a path')
    root=ROOT/'src/dashboard_GUI/var/runs' if args.scope=='app' else ROOT/'runs'
    reader=ExperimentReader(root)
    match=[v for v in reader.list() if Path(v['directory']).name==args.name]
    if len(match)!=1:parser.error('Experiment name is absent or ambiguous in this scope')
    out=ROOT/'src/dashboard_GUI/var/reports'/args.name/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:6])
    if any(p.is_symlink() for p in (out,*out.parents)):parser.error('Report output must not traverse symlinks')
    result=render(reader,match[0]['id'],out)
    print('Validated runs:',result['present_runs'],'/',result['expected_runs'])
    print('Actual orders/run:',result['facts']['actual_orders'])
    print('Preview:',result['index'])
    print('Original artifacts and old plots unchanged.')


if __name__=='__main__':main()
