"""Explicit tiny Stage 2 verification. Creates disposable app-owned outputs only.

Run from repository root with backend/.venv Python and the documented PYTHONPATH.
Not part of the default test suite: executes 3 orders x 2 robots in each parity
run, plus a two-run 1-order x 1-robot seeded matrix and its no-op resume.
"""
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

import httpx
from websockets.sync.client import connect
from control_center.adapters.catalogue import REPO
from control_center.schemas.jobs import RunSpec, ExperimentSpec
from control_center.services.jobs import JobManager, TERMINAL


def wait(manager, jid):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        job = manager.get(jid)
        if job['status'] in TERMINAL:
            assert job['status'] == 'completed', job
            return job
        time.sleep(.05)
    raise AssertionError('Tiny smoke exceeded 20 seconds; cancel/check manually: ' + jid)


def main():
    root = REPO / 'src/dashboard_GUI/var/verification' / ('stage2_' + uuid.uuid4().hex[:10])
    with JobManager(root) as manager:
        p = manager.preview(RunSpec(robots=2, orders=3, seed=7, progress_interval_s=0))
        job = wait(manager, manager.launch(p['preview_id'], 'run')['id'])
        cli_output = root / 'cli_parity'
        argv = list(p['argv'])
        argv[argv.index('--dataset') + 1] = str(cli_output)
        with (root / 'cli.log').open('wb') as log:
            done = subprocess.run(argv, cwd=REPO, env=manager.planner.environment(),
                                  stdout=log, stderr=subprocess.STDOUT, timeout=20)
        assert done.returncode == 0
        from simulation_env.dataset_export import read_run
        a = Path(job['output_dir'])
        da, oa, ma = read_run(a)
        db, ob, mb = read_run(cli_output)
        assert da == db and oa == ob and ma == mb
        host = {'wall_clock_s', 'allocator_cpu_s', 'mission_build_cpu_s', 'worst_dispatch_s'}
        metrics_a = json.loads((a / 'metrics.json').read_text())
        metrics_b = json.loads((cli_output / 'metrics.json').read_text())
        assert {k:v for k,v in metrics_a.items() if k not in host} == {k:v for k,v in metrics_b.items() if k not in host}
        result = dict(single_job=job['id'], parity=dict(decision_rows=len(da), outcome_rows=len(oa),
            deterministic_metric_keys=len(set(metrics_a) - host), metrics=metrics_a,
            host_timing_cli={k:metrics_b[k] for k in host}, host_timing_app={k:metrics_a[k] for k in host}))
        spec = ExperimentSpec(name='tiny', strategies=['nearest_astar','random'], robots=[1], seeds=[7],
                              orders=1, workload_orders=1, jobs=1)
        p = manager.preview(spec)
        experiment = wait(manager, manager.launch(p['preview_id'], 'experiment')['id'])
        p = manager.preview(spec.model_copy(update={'mode':'resume'}))
        resumed = wait(manager, manager.launch(p['preview_id'], 'experiment')['id'])
        assert '2 already done, 0 to run' in manager.logs(resumed['id'])['text']
        result.update(experiment=experiment['id'], resume=resumed['id'], output_root=str(root))
    # Actual TCP/HTTP + WebSocket backend, on an ephemeral loopback port.
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    code = ('from control_center.app import create_app;import uvicorn;'
            f'uvicorn.run(create_app(data_dir={str(root)!r}),host="127.0.0.1",port={port},proxy_headers=False)')
    with (root / 'socket.log').open('wb') as log:
        process = subprocess.Popen([sys.executable, '-c', code], cwd=REPO, stdout=log, stderr=subprocess.STDOUT)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}') as client:
                deadline = time.monotonic() + 10
                while True:
                    try:
                        response = client.get('/api/v1/health')
                        if response.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if time.monotonic() >= deadline:
                        raise AssertionError((root / 'socket.log').read_text())
                    time.sleep(.05)
                assert client.get('/api/v1/session').status_code == 200
                cookie = '; '.join(f'{k}={v}' for k,v in client.cookies.items())
                with connect(f'ws://127.0.0.1:{port}/api/v1/ws/jobs/{job["id"]}',
                             origin=f'http://127.0.0.1:{port}', additional_headers={'Cookie':cookie}) as ws:
                    event = json.loads(ws.recv(timeout=3))
                    assert event['type'] == 'job_status' and event['sequence'] == 1
                result['socket'] = dict(health_http=200, websocket_first_sequence=1, bind='127.0.0.1')
        finally:
            process.terminate()
            process.wait(timeout=10)
    (root / 'result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({k:v for k,v in result.items() if k != 'parity'}, indent=2))
    print('PASS: exact decision/outcome/manifest equality; deterministic metrics equal;',len(da),'decisions,',len(oa),'outcomes')
    print('Evidence:', root / 'result.json')


if __name__ == '__main__':
    main()
