"""Tiny stand-in processes exercise lifecycle; no scientific workloads here."""
from pathlib import Path
import subprocess
import sys
import time

import pytest
from fastapi import HTTPException

from control_center.schemas.jobs import RunSpec


class FixturePlanner:
    def __init__(self, root, code=None):
        self.data_dir = root
        self.output_root = root / 'runs'
        self.version = 'one'
        self.code = code or "print('hello from child', flush=True)"

    def build(self, spec, job_id):
        return dict(job_id=job_id, kind=spec.kind, request=spec.model_dump(), cwd=str(self.data_dir),
            argv=[sys.executable, '-u', '-c', self.code], output_dir=str(self.output_root / job_id),
            fingerprint=self.version, inputs_hash=self.version, effective={'orders': 1, 'robots': 1},
            grid=[], warnings=[], output_before=None)

    def environment(self):
        import os
        return dict(os.environ)


def wait_for(manager, jid, states, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get(jid)
        if job['status'] in states:
            return job
        time.sleep(.02)
    raise AssertionError(manager.get(jid))


def test_zero_exit_without_artifacts_is_failed_and_logs_reconnect(tmp_path):
    from control_center.services.jobs import JobManager
    with JobManager(tmp_path, planner=FixturePlanner(tmp_path)) as m:
        preview = m.preview(RunSpec())
        job = m.launch(preview['preview_id'], 'run')
        final = wait_for(m, job['id'], {'failed'})
        assert final['exit_code'] == 0 and final['artifact_status'] == 'incomplete'
        logs = m.logs(job['id'], 0, 4)
        assert logs['text'] == 'hell'
        tail = m.logs(job['id'], logs['next_cursor'], 65536)
        assert 'o from child' in tail['text']
        assert any(e['type'] == 'job_status' for e in m.events(job['id'], 0)['events'])
        assert m.get(job['id'])['status'] == 'failed'
    with JobManager(tmp_path, planner=FixturePlanner(tmp_path)) as restarted:
        assert restarted.get(job['id'])['exit_code'] == 0
        assert 'hello from child' in restarted.logs(job['id'], 0, 65536)['text']


def test_preview_rechecks_inputs_and_single_active_job(tmp_path):
    from control_center.services.jobs import JobManager
    planner = FixturePlanner(tmp_path, 'import time; time.sleep(30)')
    with JobManager(tmp_path, planner=planner, grace_s=.1) as m:
        p = m.preview(RunSpec())
        planner.version = 'changed'
        with pytest.raises(HTTPException) as caught:
            m.launch(p['preview_id'], 'run')
        assert caught.value.status_code == 409
        p = m.preview(RunSpec())
        j = m.launch(p['preview_id'], 'run')
        with pytest.raises(HTTPException):
            m.launch(m.preview(RunSpec())['preview_id'], 'run')
        m.cancel(j['id'])
        assert wait_for(m, j['id'], {'cancelled'})['exit_code'] is not None
        with pytest.raises(HTTPException):
            m.launch(p['preview_id'], 'run')


def test_cancellation_escalates_to_kill_owned_descendants(tmp_path):
    from control_center.services.jobs import JobManager
    code = """import subprocess,sys,time,signal
signal.signal(signal.SIGTERM, signal.SIG_IGN)
p=subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM, signal.SIG_IGN);time.sleep(30)'])
print('descendant='+str(p.pid),flush=True)
time.sleep(30)
"""
    with JobManager(tmp_path, planner=FixturePlanner(tmp_path, code), grace_s=.15) as m:
        job = m.launch(m.preview(RunSpec())['preview_id'], 'run')
        deadline = time.monotonic() + 3
        while 'descendant=' not in m.logs(job['id'], 0, 65536)['text'] and time.monotonic() < deadline:
            time.sleep(.02)
        text = m.logs(job['id'], 0, 65536)['text']
        assert 'descendant=' in text
        pid = int(text.split('descendant=')[1].splitlines()[0])
        m.cancel(job['id'])
        assert wait_for(m, job['id'], {'cancelled'})['exit_code'] == -9
        stat = Path(f'/proc/{pid}/stat')
        assert not stat.exists() or stat.read_text().split(') ')[1][0] == 'Z'


def test_exclusive_manager_lock_and_unknown_job(tmp_path):
    from control_center.services.jobs import JobManager
    with JobManager(tmp_path, planner=FixturePlanner(tmp_path)) as m:
        with pytest.raises(RuntimeError):
            JobManager(tmp_path, planner=FixturePlanner(tmp_path))
        with pytest.raises(HTTPException) as caught:
            m.cancel('not-ours')
        assert caught.value.status_code == 404


def test_restart_does_not_signal_reused_or_unowned_pid(tmp_path):
    from control_center.services.jobs import JobManager
    from control_center.storage.jobs import JobStore
    from control_center.services.processes import identity
    proc = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'], start_new_session=True)
    try:
        ident = identity(proc.pid)
        ident['start'] += 1  # PID matches, process identity does not.
        store = JobStore(tmp_path)
        store.save(dict(id='orphan', status='running', process_identity=ident, sequence=0,
                        created_at='now', kind='run', output_dir=str(tmp_path / 'runs/x'),
                        log_path=str(tmp_path / 'missing.log')))
        with JobManager(tmp_path, planner=FixturePlanner(tmp_path)) as m:
            assert m.get('orphan')['status'] == 'interrupted'
            with pytest.raises(HTTPException):
                m.cancel('orphan')
            assert proc.poll() is None
    finally:
        proc.terminate()
        proc.wait(timeout=3)


def test_parquet_validation_survives_successive_worker_threads(tmp_path):
    """A native-reader crash must not terminate the API after its first job."""
    import os
    from test_backend import fixture_run
    for name in ('one', 'two', 'three'):
        fixture_run(tmp_path / 'history', name=name, fmt='parquet')
    code = '''import sys,threading
from pathlib import Path
from control_center.storage.index import RunIndex
from simulation_env.dataset_export import read_run
from control_center.services.jobs import JobManager
r=Path(sys.argv[1]); errors=[]
manager=JobManager(r/'app')
def scan(name):
    try: assert len(RunIndex(r/'history'/name,r/('index_'+name)).rebuild()) == 1
    except BaseException as exc: errors.append(exc)
t=threading.Thread(target=scan,args=('one',));t.start();t.join()
read_run(r/'history/one')
t=threading.Thread(target=scan,args=('two',));t.start();t.join()
assert not errors,errors
manager.close()
'''
    done = subprocess.run([sys.executable, '-X', 'faulthandler', '-c', code, str(tmp_path)],
                          capture_output=True, timeout=10, env=dict(os.environ))
    assert done.returncode == 0, done.stderr.decode()[-2500:]


def test_recovered_owned_process_blocks_new_launch_and_can_be_cancelled(tmp_path):
    from control_center.services.jobs import JobManager
    from control_center.storage.jobs import JobStore
    from control_center.services.processes import identity
    proc = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'], start_new_session=True)
    try:
        JobStore(tmp_path).save(dict(id='recovered', status='running', process_identity=identity(proc.pid),
                                   sequence=0, kind='run', output_dir=str(tmp_path / 'runs/x'),
                                   log_path=str(tmp_path / 'missing.log')))
        with JobManager(tmp_path, planner=FixturePlanner(tmp_path), grace_s=.1) as m:
            assert m.get('recovered')['status'] == 'interrupted'
            with pytest.raises(HTTPException):
                m.launch(m.preview(RunSpec())['preview_id'], 'run')
            m.cancel('recovered')
            final = wait_for(m, 'recovered', {'cancelled'})
            assert final['exit_code'] is None  # no invented exit code after restart
        proc.wait(timeout=3)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)


def test_log_limit_cancels_and_retained_event_gaps_are_explicit(tmp_path):
    from control_center.services.jobs import JobManager
    code = 'import time;print("x"*2048,flush=True);time.sleep(30)'
    with JobManager(tmp_path / 'limit', planner=FixturePlanner(tmp_path / 'limit', code),
                    grace_s=.1, max_log_bytes=1024) as m:
        job = m.launch(m.preview(RunSpec())['preview_id'], 'run')
        final = wait_for(m, job['id'], {'cancelled'})
        assert 'log limit' in final['error']
    code = 'import sys;sys.stdout.write("x"*(3*1024*1024));sys.stdout.flush()'
    with JobManager(tmp_path / 'gap', planner=FixturePlanner(tmp_path / 'gap', code)) as m:
        job = m.launch(m.preview(RunSpec())['preview_id'], 'run')
        wait_for(m, job['id'], {'failed'})
        page = m.events(job['id'], 0)
        assert page['resync_required'] is True
        assert m.logs(job['id'], 0, 65536)['next_cursor'] == 65536
        latest = m.get(job['id'])['sequence']
        assert m.events(job['id'], latest)['events'] == []


def test_monitor_error_still_reaps_term_resistant_descendant(tmp_path, monkeypatch):
    from control_center.services.jobs import JobManager
    from control_center.services.processes import signal_group
    code = """import subprocess,sys,time
p=subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);print("child-ready",flush=True);time.sleep(30)'])
time.sleep(30)
"""
    m = JobManager(tmp_path, planner=FixturePlanner(tmp_path, code), grace_s=.1)
    ident = None
    try:
        job = m.launch(m.preview(RunSpec())['preview_id'], 'run')
        ident = job['process_identity']
        deadline = time.monotonic() + 3
        while 'child-ready' not in m.logs(job['id'])['text'] and time.monotonic() < deadline:
            time.sleep(.02)
        assert 'child-ready' in m.logs(job['id'])['text']
        def broken(*args):
            raise RuntimeError('injected log read failure')
        monkeypatch.setattr(m, '_harvest', broken)
        final = wait_for(m, job['id'], {'interrupted'})
        assert final['owned_process_alive'] is False
    finally:
        signal_group(ident, force=True)
        m.close()


def test_backend_crash_in_spawn_window_cannot_start_untracked_science(tmp_path):
    import os
    import signal
    marker = tmp_path / 'science_started'
    script = '''import os,sys
from pathlib import Path
from test_jobs import FixturePlanner
from control_center.services.jobs import JobManager
from control_center.schemas.jobs import RunSpec
import control_center.services.jobs as module
root=Path(sys.argv[1]); marker=root/'science_started'
code='import os,time;from pathlib import Path;Path('+repr(str(marker))+').write_text(str(os.getpid()));time.sleep(30)'
m=JobManager(root,planner=FixturePlanner(root,code))
real=module.subprocess.Popen
def crash_after_spawn(*a,**kw):
    real(*a,**kw)
    os._exit(0)
module.subprocess.Popen=crash_after_spawn
m.launch(m.preview(RunSpec())['preview_id'],'run')
'''
    env = dict(os.environ)
    env['PYTHONPATH'] = str(Path(__file__).parent.absolute()) + os.pathsep + env['PYTHONPATH']
    done = subprocess.run([sys.executable, '-c', script, str(tmp_path)], env=env, capture_output=True, timeout=5)
    assert done.returncode == 0, done.stderr.decode()
    try:
        time.sleep(.2)  # Give the spawned gate time to publish readiness.
        assert not marker.exists(), 'Original CLI ran before process ownership was persisted'
        from control_center.services.jobs import JobManager
        with JobManager(tmp_path, planner=FixturePlanner(tmp_path), grace_s=.1) as m:
            recovered = m.list()[0]
            assert recovered['status'] == 'interrupted'
            assert recovered['owned_process_alive'] is True
            m.cancel(recovered['id'])
            wait_for(m, recovered['id'], {'cancelled'})
        assert not marker.exists()
    finally:
        if marker.exists():
            try:
                os.killpg(int(marker.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
