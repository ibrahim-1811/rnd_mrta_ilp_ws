"""Explicit real production launcher/proxy/security/shutdown integration check.

No simulations or mutations of scientific artifacts. Requires free 8000/5173.
Temporary local servers are stopped in finally; evidence stays under var/.
"""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

from launch_control_center import ROOT, check_ports, environment, shutdown


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir',required=True,type=Path)
    args=parser.parse_args();folder=args.evidence_dir.absolute()
    # This command writes evidence only to the app-owned runtime root.
    runtime_root=ROOT/'src/dashboard_GUI/var'
    if runtime_root not in folder.parents or any(p.is_symlink() for p in (folder,*folder.parents)):
        raise RuntimeError('Evidence directory must be beneath dashboard_GUI/var without symlinks')
    folder.mkdir(parents=True,exist_ok=False);check_ports()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(path,headers=None,body=None):
        req=urllib.request.Request('http://127.0.0.1:5173'+path,data=body,headers=headers or {})
        try:
            with opener.open(req,timeout=3) as response:return response.status,response.read(),response.headers
        except urllib.error.HTTPError as exc:return exc.code,exc.read(),exc.headers
    result={}
    with (folder/'launcher.log').open('xb') as log:
        process=subprocess.Popen([sys.executable,str(ROOT/'src/dashboard_GUI/scripts/launch_control_center.py'),'start'],
            cwd=ROOT,env=environment(),stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            deadline=time.monotonic()+45
            while True:
                if process.poll() is not None:raise RuntimeError('Launcher exited; inspect '+str(folder/'launcher.log'))
                try:
                    status,body,_=request('/api/v1/health')
                    if status==200 and json.loads(body)['data']['status']=='ok':break
                except (OSError,ValueError,urllib.error.URLError):pass
                if time.monotonic()>deadline:raise RuntimeError('Production startup exceeded readiness bound')
                time.sleep(.1)
            status,body,_=request('/')
            assert status==200 and b'id="root"' in body and b'MRTA' in body
            scripts=re.findall(rb'<script[^>]*src="([^"]+)"',body)
            assert scripts,'Built JavaScript asset missing from index'
            for asset in scripts:
                path=asset.decode();assert path.startswith('/assets/')
                code,data,_=request(path);assert code==200 and len(data)>1000
            result.update(frontend_index=200,built_assets=len(scripts),proxy_health=200)
            assert request('/api/v1/session',{'Origin':'https://untrusted.example'})[0]==403
            bad_host=request('/api/v1/health',{'Host':'untrusted.example'})[0]
            assert bad_host in (400,403)  # Vite may reject before backend Host validation.
            assert request('/api/v1/jobs/preview',{'Content-Type':'application/json'},b'{}')[0]==403
            status,body,headers=request('/api/v1/session',{'Origin':'http://127.0.0.1:5173'})
            assert status==200 and json.loads(body)['data']['csrf_token'] and 'HttpOnly' in headers.get('Set-Cookie','')
            result.update(bad_origin=403,bad_host=bad_host,no_csrf=403,same_origin_session=200)
            # If there is existing history, also check the real same-origin WS proxy.
            _,body,_=request('/api/v1/jobs?limit=1');jobs=json.loads(body)['data']
            result['websocket_proxy']='no existing job; standalone parity gate covers WebSocket'
            if jobs:
                from websockets.sync.client import connect
                cookie=headers['Set-Cookie'].split(';')[0]
                with connect('ws://127.0.0.1:5173/api/v1/ws/jobs/'+jobs[0]['id'],
                             origin='http://127.0.0.1:5173',additional_headers={'Cookie':cookie}) as ws:
                    event=json.loads(ws.recv(timeout=4))
                    assert event.get('type') in ('job_status','log','fleet_snapshot','resync_required','artifact_available','telemetry_gap')
                    result['websocket_proxy']='passed'
        finally:
            shutdown([('launcher',process)], timeout=25)
    check_ports()
    result.update(launcher_exit=process.returncode,ports_released=True)
    assert process.returncode==0
    (folder/'result.json').write_text(json.dumps(result,indent=2))
    print('PASS: production UI assets, real proxy/session/Host/Origin/CSRF checks, owned shutdown and free ports')
    print('Evidence:',folder/'result.json')


if __name__=='__main__':
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    main()
