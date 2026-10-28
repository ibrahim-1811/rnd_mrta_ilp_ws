"""Bounded read-only experiment views; original per-run artifacts and paired math."""
import os
from pathlib import Path
import re
from fastapi import HTTPException
from control_center.services.pagination import fingerprint
from control_center.storage.files import open_source, read_json
from control_center.storage.index import ArtifactReader

NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$')


class ExperimentReader:
    def __init__(self, root):
        self.root = Path(root).absolute()

    def list(self):
        found=[]; visited=0
        for folder, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:]=sorted(d for d in dirs if not (Path(folder)/d).is_symlink())
            visited+=1
            if visited>10000: raise HTTPException(413,'Experiment discovery exceeds 10000 directories')
            if 'experiment.json' not in files: continue
            directory=str(Path(folder).relative_to(self.root))
            try:
                with open_source(self.root,str(Path(directory)/'experiment.json')) as (h,_): m=read_json(h)
                if not isinstance(m.get('spec'),dict) or not isinstance(m.get('name',directory),str): continue
                found.append(dict(id=fingerprint([str(self.root),directory])[:32],directory=directory,
                                  name=m.get('name',directory),spec=m.get('spec',{}),created=m.get('created'),
                                  expected_runs=len(m.get('runs',[])) if isinstance(m.get('runs'),list) else None))
            except (HTTPException,ValueError,TypeError): continue
            if len(found)>200: raise HTTPException(413,'More than 200 experiments; use a smaller history root')
        return sorted(found,key=lambda r:r['directory'])

    def detail(self,eid):
        item=next((v for v in self.list() if v['id']==eid),None)
        if item is None: raise HTTPException(404,'Unknown scoped experiment ID')
        directory=item['directory']
        with open_source(self.root,str(Path(directory)/'experiment.json')) as (h,_): manifest=read_json(h)
        spec=manifest.get('spec')
        if not isinstance(spec,dict):raise HTTPException(409,'Invalid experiment specification')
        if 'strategies' in spec and (not isinstance(spec['strategies'],list) or not all(isinstance(v,str) and NAME.fullmatch(v) for v in spec['strategies'])):raise HTTPException(409,'Invalid strategy specification')
        item = item | dict(spec=spec, name=manifest.get('name',item['name']))
        grid=manifest.get('runs')
        if not isinstance(grid,list) or not 1<=len(grid)<=5000: raise HTTPException(409,'Invalid or oversized experiment grid')
        checked=[]
        for row in grid:
            if (not isinstance(row,list) or len(row)!=3 or not isinstance(row[0],str) or not NAME.fullmatch(row[0])
                or type(row[1]) is not int or not 1<=row[1]<=1000 or type(row[2]) is not int or not 0<=row[2]<2**32):
                raise HTTPException(409,'Invalid experiment grid identity')
            checked.append(tuple(row))
        if len(set(checked))!=len(checked): raise HTTPException(409,'Duplicate experiment grid entries')
        from simulation_env.paired import settings_signature
        reader=ArtifactReader(self.root); results=[]; values=[]
        for strategy,robots,seed in checked:
            rel=str(Path(directory)/strategy/f'r{robots}_s{seed}')
            record=reader.inspect(rel)
            state='missing';reason='No valid complete scientific dataset'
            if record['process_status'] in ('stalled','failed','cancelled'):state=record['process_status']
            elif record['integrity']=='complete' and record['provenance']=='present':
                try:
                    with open_source(self.root,str(Path(rel)/'run.json')) as (h,sig):
                        if sig!=next(a['signature'] for a in record['artifacts'] if a['id']=='run.json'):raise HTTPException(409,'Manifest changed during experiment inspection')
                        m=read_json(h)
                    with open_source(self.root,str(Path(rel)/'metrics.json')) as (h,sig):
                        if sig!=next(a['signature'] for a in record['artifacts'] if a['id']=='metrics.json'):raise HTTPException(409,'Metrics changed during experiment inspection')
                        v=read_json(h)
                    if any(m.get(k)!=val for k,val in [('strategy',strategy),('robot_count',robots),('seed',seed)]):raise ValueError('Run manifest identity differs from grid')
                    if any(v.get(k)!=val for k,val in [('strategy',strategy),('robots',robots),('seed',seed)]):raise ValueError('Metric identity differs from grid')
                    if not isinstance(m.get('git'),dict) or not isinstance(m['git'].get('commit'),str) or not m['git']['commit'] or any(m.get(k) is None for k in ('orders_sha256','line_items','window_width')):raise ValueError('Missing pairing provenance')
                    # Match compare_paired.load numeric conversion without converting identity strings.
                    row={k:float(x) if type(x) in (int,float) else x for k,x in v.items()}
                    row.update(strategy=strategy,robots=float(robots),seed=float(seed),workload=manifest.get('spec',{}).get('workloads'),
                               orders_sha256=m['orders_sha256'],line_items=float(m['line_items']),window_width=float(m['window_width']),
                               settings_sha256=settings_signature(m))
                    values.append(row);state='present';reason=None
                except (HTTPException,ValueError,TypeError,KeyError) as exc: state='invalid';reason=str(exc)
            results.append(dict(strategy=strategy,robots=robots,seed=seed,status=state,reason=reason,run_id=record['id'],
                                directory=rel,integrity=record['integrity'],warnings=record['warnings']))
        expected_paths={r['directory'] for r in results};extra=[]
        base=self.root/directory
        for folder,dirs,files in os.walk(base,followlinks=False):
            dirs[:]=[d for d in dirs if not (Path(folder)/d).is_symlink()]
            if 'run.json' in files and str(Path(folder).relative_to(self.root)) not in expected_paths:
                extra.append(str(Path(folder).relative_to(self.root)))
                if len(extra)>5000:raise HTTPException(413,'Too many extra run directories')
        return item|dict(manifest=manifest,runs=results,complete=all(r['status']=='present' for r in results) and not extra,
                         excluded=[r for r in results if r['status']!='present'],extra=extra,rows=values,
                         warnings=['Dirty-source contents are not an immutable archive; examine original provenance.',
                                   'Original per-run metrics are authoritative; summary.csv is not repaired or trusted as an identity source.'])

    def compare(self,eid,baseline,allow_incomplete):
        from simulation_env.paired import check_complete,check_settings,paired_differences,PairingError
        detail=self.detail(eid);rows=detail['rows'];grid=detail['manifest']['runs']
        if detail['spec'].get('workloads')!='seeded':raise HTTPException(409,'Paired analysis requires seeded workloads')
        if baseline not in {s for s,_,_ in grid}:raise HTTPException(422,'Baseline is outside the experiment grid')
        if not allow_incomplete and not detail['complete']:raise HTTPException(409,'Incomplete experiment; inspect missing/invalid runs or explicitly allow incomplete analysis')
        try:
            if not allow_incomplete:check_complete(rows,grid)
            check_settings(rows)
            records=paired_differences(rows,baseline)
        except PairingError as exc:raise HTTPException(409,str(exc)) from exc
        for row in records:
            want={seed for s,r,seed in grid if s==row['strategy'] and r==row['robots']} & {seed for s,r,seed in grid if s==baseline and r==row['robots']}
            row.update(expected_n=len(want),complete=row['n']==len(want))
        return dict(records=records,complete=detail['complete'],excluded=detail['excluded'],extra=detail['extra'],
                    warnings=detail['warnings'],baseline=baseline,expected_runs=len(grid),present_runs=len(rows),
                    method='simulation_env.paired.paired_differences; strategy minus baseline; paired t interval, 95%')
