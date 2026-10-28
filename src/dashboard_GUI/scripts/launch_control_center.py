#!/usr/bin/env python3
"""Local foreground launcher and explicit verification gate. No auto-install/run."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import threading
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src/dashboard_GUI/backend'))
from control_center.services.processes import identity, members, signal_group


def environment():
    # Preserve desktop/shell paths, discard Python/Node injection into scientific
    # imports and fix the project roots, independent of the caller's directory.
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PYTHON', 'NODE_'))}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1',
        PYTHONPATH=os.pathsep.join(str(ROOT / 'src' / p) for p in
                     ('dashboard_GUI/backend','mrta_core','simulation_env','task_management')))
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    return env


def paths():
    front = ROOT / 'src/dashboard_GUI/frontend'
    python = ROOT / 'src/dashboard_GUI/backend/.venv/bin/python'
    node, npm = shutil.which('node'), shutil.which('npm')
    if not python.is_file():
        raise RuntimeError('Missing backend interpreter: backend/.venv/bin/python; see OPERATIONS.md')
    if not (ROOT / '.pixi/envs/default/bin/python').is_file():
        raise RuntimeError('Missing scientific interpreter: .pixi/envs/default/bin/python; restore the existing project environment')
    if not node or not npm:
        raise RuntimeError('Node.js and npm are required on PATH; use Node 22.12+ and see OPERATIONS.md')
    if not (front / 'node_modules/vite/bin/vite.js').is_file():
        raise RuntimeError('Missing frontend dependencies; run npm ci inside src/dashboard_GUI/frontend')
    return dict(front=front, python=str(python), node=node, npm=npm)


def check_ports(ports=(8000, 5173)):
    for port in ports:
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(('127.0.0.1', port))
            except OSError as exc:
                raise RuntimeError(f'Local port {port} is occupied/unavailable. Stop your existing instance from its terminal; no process was signalled.') from exc


def dependencies(runtime):
    output = subprocess.run([runtime['node'], '--version'], capture_output=True, text=True, timeout=10, env=environment(), cwd=ROOT)
    try:
        major, minor, *_ = map(int, output.stdout.strip().lstrip('v').split('.'))
    except ValueError as exc:
        raise RuntimeError('Cannot determine Node.js version') from exc
    if output.returncode or major < 22 or (major == 22 and minor < 12):
        raise RuntimeError('Node.js 22.12+ required by this frontend; no packages were installed')
    code = ('import fastapi,uvicorn,websockets,pyarrow.parquet,scipy;'
            'import mrta_core.sim_config,simulation_env.dataset,task_management.allocation_strategy;'
            'from control_center.adapters.catalogue import load_scenario;load_scenario()')
    result = subprocess.run([runtime['python'], '-c', code], capture_output=True, text=True,
                            timeout=30, env=environment(), cwd=ROOT)
    if result.returncode:
        raise RuntimeError('Backend/scientific dependency check failed:\n' + result.stderr[-3000:])
    print('Dependencies: backend/scientific imports, W100 input/map and Node.js ' + output.stdout.strip(), flush=True)


def logs_directory():
    folder = ROOT / 'src/dashboard_GUI/var/launcher'
    if any(p.is_symlink() for p in (folder, *folder.parents)):
        raise RuntimeError('Launcher log directory must not contain a symlink')
    folder.mkdir(parents=True, exist_ok=True)
    folder = folder / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
    folder.mkdir()
    return folder


def spawn(argv, cwd, log):
    process = subprocess.Popen(argv, cwd=cwd, env=environment(), stdin=subprocess.DEVNULL,
                               stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    process.dashboard_identity = identity(process.pid)
    return process


def shutdown(children, timeout=12):
    # Finish the bounded escalation/reap even if Ctrl+C is pressed twice.
    saved = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            saved[sig] = signal.signal(sig, signal.SIG_IGN)
    try:
        _shutdown(children, timeout)
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)


def _shutdown(children, timeout):
    # Exact Popen handles + recorded Linux session identity; never adopt a port/PID.
    for _, process in reversed(children):
        owned = getattr(process, 'dashboard_identity', None)
        if owned:
            signal_group(owned)
        elif process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + timeout
    for _, process in reversed(children):
        owned = getattr(process, 'dashboard_identity', None)
        while time.monotonic() < deadline and ((members(owned) if owned else []) or process.poll() is None):
            time.sleep(.03)
        if owned:
            signal_group(owned, force=True)
        elif process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            print('Child shutdown exceeded wait bound; inspect its launcher log.', file=sys.stderr)


def wait_ready(children, url, *, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for name, process in children:
            if process.poll() is not None:
                raise RuntimeError(f'{name} exited before readiness (code {process.returncode}); inspect launcher logs')
        try:
            # Disable inherited proxy settings for loopback readiness.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url, timeout=.4) as response:
                data = json.load(response)
            if data.get('api_schema_version') == 1 and data.get('data', {}).get('status') == 'ok':
                return
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(.08)
    raise RuntimeError(f'Timed out waiting for {url}; inspect launcher logs')


def run_step(name, argv, cwd, folder, *, timeout=180):
    print(name + '…', flush=True)
    path = folder / (name.replace(' ', '-').lower() + '.log')
    with path.open('xb') as log:
        process = spawn(argv, cwd, log)
        try:
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f'{name} exceeded {timeout}s; log: {path}') from exc
            if code:
                raise RuntimeError(f'{name} failed (code {code}); log: {path}\n' + path.read_text(errors='replace')[-3500:])
        finally:
            shutdown([(name, process)], timeout=2)
    print(name + ': PASS', flush=True)


def start(runtime, *, dev=False):
    check_ports()
    folder = logs_directory()
    children, handles = [], []
    print('Launcher logs: ' + str(folder), flush=True)
    try:
        if not dev:
            run_step('Frontend build', [runtime['npm'], 'run', 'build'], runtime['front'], folder)
        check_ports()  # Ports may have become occupied during the build.
        for name, argv, cwd in [
            ('backend', [runtime['python'], '-m', 'control_center.app'], ROOT),
            ('frontend', [runtime['node'], str(runtime['front'] / 'node_modules/vite/bin/vite.js'),
                          *([] if dev else ['preview']), '--host', '127.0.0.1', '--port', '5173', '--strictPort'], runtime['front'])]:
            handle = (folder / (name + '.log')).open('xb'); handles.append(handle)
            children.append((name, spawn(argv, cwd, handle)))
        wait_ready(children, 'http://127.0.0.1:8000/api/v1/health')
        wait_ready(children, 'http://127.0.0.1:5173/api/v1/health')
        print('Dashboard ready: http://127.0.0.1:5173/\nAPI reference: http://127.0.0.1:8000/docs\nKeep this terminal open. Ctrl+C stops both services and requests cancellation of owned active jobs.', flush=True)
        while True:
            for name, process in children:
                if process.poll() is not None:
                    raise RuntimeError(f'{name} exited (code {process.returncode}); shutting down the other service. Logs: {folder}')
            time.sleep(.2)
    except KeyboardInterrupt:
        print('\nStopping owned services…', flush=True)
        return 0
    finally:
        shutdown(children)
        for handle in handles:
            handle.close()
        print('Owned services stopped. Logs retained: ' + str(folder), flush=True)


def verify(runtime, *, skip_parity=False):
    check_ports()
    folder = logs_directory()
    print('Verification logs: ' + str(folder), flush=True)
    print('Verification executes tests and tiny disposable scientific fixtures; it never runs a large study.', flush=True)
    steps = [
        ('Backend tests', [runtime['python'], '-m', 'pytest', '-q', 'src/dashboard_GUI/backend/tests'], ROOT),
        ('Frontend tests', [runtime['npm'], 'test'], runtime['front']),
        ('Frontend build', [runtime['npm'], 'run', 'build'], runtime['front'])]
    if not skip_parity:
        steps.append(('Tiny observed CLI parity', [runtime['python'], 'src/dashboard_GUI/scripts/check_stage3_smoke.py'], ROOT))
    steps.append(('Local HTTP integration', [runtime['python'], 'src/dashboard_GUI/scripts/check_stage6_http.py', '--evidence-dir', str(folder / 'http-integration')], ROOT))
    for name, argv, cwd in steps:
        run_step(name, argv, cwd, folder)
    (folder / 'result.json').write_text(json.dumps(dict(passed=True, checks=[name for name, _, _ in steps],
                                                     parity_skipped=skip_parity), indent=2))
    print('PASS: verification gate. No application servers left running. Evidence: ' + str(folder / 'result.json'), flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check', help='Check dependencies and free ports; no install, build, server or simulation')
    launch = sub.add_parser('start', help='Build UI and run both loopback services in this terminal')
    launch.add_argument('--dev', action='store_true', help='Use Vite development mode without production build')
    gate = sub.add_parser('verify', help='Tests/build plus explicit tiny observed/CLI parity and HTTP/WebSocket check')
    gate.add_argument('--skip-parity', action='store_true', help='Skip standalone smoke (backend tests still include tiny fixtures)')
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        runtime = paths(); dependencies(runtime)
        if args.command == 'check':
            check_ports(); print('Ready to start: ports 8000/5173 free. No servers or simulations started.'); return 0
        if args.command == 'start': return start(runtime, dev=args.dev)
        return verify(runtime, skip_parity=args.skip_parity)
    except KeyboardInterrupt:
        print('\nInterrupted; owned verification children were stopped.', file=sys.stderr); return 130
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print('Control Center: ' + str(exc), file=sys.stderr); return 1


if __name__ == '__main__':
    raise SystemExit(main())
