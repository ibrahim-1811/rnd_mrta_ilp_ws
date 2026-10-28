import csv
import hashlib
import json
import pytest
# Initialize the native reader on pytest's owner thread before TestClient's lifespan thread.
import pyarrow.parquet  # noqa: F401
from fastapi import HTTPException
from test_backend import fixture_run


def experiment(root):
    base = root / 'study'
    grid = [[s, 2, seed] for s in ('nearest_astar', 'random') for seed in (1, 2)]
    base.mkdir(parents=True)
    (base/'experiment.json').write_text(json.dumps(dict(name='study',spec={'workloads':'seeded'},runs=grid,git={'dirty':True})))
    for strategy, robots, seed in grid:
        path = fixture_run(base, f'{strategy}/r{robots}_s{seed}')
        m=json.loads((path/'run.json').read_text());m.update(strategy=strategy,robot_count=robots,seed=seed,orders_sha256=f'{seed:064}',line_items=1,window_width=robots,dispatch_quantum_s=0)
        (path/'run.json').write_text(json.dumps(m))
        v=json.loads((path/'metrics.json').read_text());v.update(strategy=strategy,robots=robots,seed=seed,order_makespan_s=10+(strategy=='random')*seed)
        (path/'metrics.json').write_text(json.dumps(v))
    return base


def test_original_paired_math_and_no_writes(tmp_path):
    from control_center.adapters.experiments import ExperimentReader
    experiment(tmp_path)
    reader=ExperimentReader(tmp_path)
    listing=reader.list(); assert len(listing)==1
    before={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    detail=reader.detail(listing[0]['id']); assert detail['complete'] and len(detail['runs'])==4
    result=reader.compare(listing[0]['id'],'nearest_astar',False)
    record=next(r for r in result['records'] if r['metric']=='order_makespan_s')
    assert record['mean_difference']==1.5 and record['n']==2 and record['expected_n']==2
    assert before=={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


def test_missing_and_mixed_quantum_refused(tmp_path):
    from control_center.adapters.experiments import ExperimentReader
    base=experiment(tmp_path);reader=ExperimentReader(tmp_path);eid=reader.list()[0]['id']
    (base/'random/r2_s2/metrics.json').unlink()
    with pytest.raises(HTTPException) as exc:reader.compare(eid,'nearest_astar',False)
    assert exc.value.status_code==409
    partial=reader.compare(eid,'nearest_astar',True)
    assert not partial['complete'] and len(partial['excluded'])==1
    assert next(r for r in partial['records'] if r['metric']=='order_makespan_s')['n']==1
    p=base/'random/r2_s1/run.json';m=json.loads(p.read_text());m['dispatch_quantum_s']=3;p.write_text(json.dumps(m))
    with pytest.raises(HTTPException) as exc:reader.compare(eid,'nearest_astar',True)
    assert exc.value.status_code==409


def test_bad_grid_and_symlinks_are_not_authorization(tmp_path):
    from control_center.adapters.experiments import ExperimentReader
    base=experiment(tmp_path);reader=ExperimentReader(tmp_path);eid=reader.list()[0]['id']
    p=base/'experiment.json';m=json.loads(p.read_text());m['runs'][0][0]='../../escape';p.write_text(json.dumps(m))
    with pytest.raises(HTTPException):reader.detail(eid)
    p.unlink();p.symlink_to('/etc/passwd')
    assert not reader.list()


def test_immutable_csv_exact_columns_map_validation_and_drift(tmp_path):
    from control_center.adapters.inputs import InputStore
    from control_center.adapters.catalogue import load_scenario
    from mrta_core.warehouse_map import ZoneType
    _,warehouse=load_scenario()
    shelf=next(z.zone_id for z in warehouse.zones if z.zone_type==ZoneType.SHELF)
    drop=next(z.zone_id for z in warehouse.zones if z.zone_type==ZoneType.DROPOFF)
    text=f'order_id,item_id,shelf_id,quantity,dropoff_id\no1,item,{shelf},1,{drop}\n'
    store=InputStore(tmp_path);data=store.add(text);path=store.resolve(data['id'])
    assert data['orders']==1 and data['sha256']==hashlib.sha256(text.encode()).hexdigest()
    assert path.read_text()==text
    assert store.add(text)['id']==data['id']
    for invalid in [text.replace('quantity,','quantity,priority,'),text.replace(',1,',',0,'),text.replace(shelf,'nonexistent')]:
        with pytest.raises(HTTPException):store.add(invalid)
    with pytest.raises(HTTPException):store.resolve('../bad')
    path.chmod(0o600);path.write_text('tampered')
    with pytest.raises(HTTPException) as exc:store.resolve(data['id'])
    assert exc.value.status_code==409


def test_csv_input_maps_original_cli_and_rechecks_hash(tmp_path):
    from control_center.adapters.inputs import InputStore
    from control_center.adapters.commands import CommandPlanner
    from control_center.adapters.catalogue import load_scenario
    from control_center.schemas.jobs import RunSpec
    config,_=load_scenario();data=InputStore(tmp_path).add(config.orders_path.read_text())
    spec=RunSpec(robots=2,orders=3,input_id=data['id'])
    plan=CommandPlanner(tmp_path).build(spec,'abc')
    assert '--orders-file' in plan['argv'] and plan['effective']['orders']==3
    path=InputStore(tmp_path).resolve(data['id']);path.chmod(0o600);path.write_text('changed')
    with pytest.raises(HTTPException):CommandPlanner(tmp_path).build(spec,'abc')


def test_research_api_scope_and_csrf(tmp_path):
    from fastapi.testclient import TestClient
    from control_center.app import create_app
    experiment(tmp_path/'history')
    with TestClient(create_app(history_root=tmp_path/'history',data_dir=tmp_path/'var'),base_url='http://127.0.0.1:8000',client=('127.0.0.1',5555)) as c:
        listed=c.get('/api/v1/experiments?scope=history').json()['data']
        eid=listed[0]['id']
        assert c.get(f'/api/v1/experiments/{eid}/comparisons?scope=history').status_code==200
        assert c.get(f'/api/v1/experiments/{eid}?scope=app').status_code==404
        assert c.post('/api/v1/inputs',json={'csv_text':'bad'}).status_code==403
        token=c.get('/api/v1/session').json()['data']['csrf_token']
        assert c.post('/api/v1/inputs',json={'csv_text':'bad'},headers={'X-Control-Center-Token':token}).status_code==422


@pytest.mark.parametrize('mode',['cli','observed'])
def test_custom_csv_run_can_finish_validation(tmp_path,mode):
    from control_center.adapters.inputs import InputStore
    from control_center.adapters.catalogue import load_scenario
    from control_center.services.jobs import JobManager
    from control_center.schemas.jobs import RunSpec
    from test_jobs import wait_for
    config,_=load_scenario()
    lines=config.orders_path.read_text().splitlines()
    selected='\n'.join(lines[:2])+'\n'
    data=InputStore(tmp_path).add(selected)
    with JobManager(tmp_path) as manager:
        preview=manager.preview(RunSpec(robots=2,orders=1,seed=7,input_id=data['id'],execution_mode=mode))
        started=manager.launch(preview['preview_id'],'run')
        finished=wait_for(manager,started['id'],{'completed','failed','interrupted'},timeout=20)
        assert finished['status']=='completed',finished.get('error')
        assert finished['artifact_status']=='complete'


def test_malformed_nested_provenance_is_excluded_not_crash(tmp_path):
    from control_center.adapters.experiments import ExperimentReader
    base=experiment(tmp_path);reader=ExperimentReader(tmp_path);eid=reader.list()[0]['id']
    path=base/'random/r2_s1/run.json';m=json.loads(path.read_text());m['git']='legacy';path.write_text(json.dumps(m))
    detail=reader.detail(eid)
    assert next(r for r in detail['runs'] if r['strategy']=='random' and r['seed']==1)['status']=='invalid'
