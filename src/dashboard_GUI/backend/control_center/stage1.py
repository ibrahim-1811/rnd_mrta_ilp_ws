"""Explicit Stage 1 ASGI entrypoint; no job lifecycle or runner imports.

The checkout also contains pre-existing Stage 2 work. Register only the reviewed
readers here rather than importing its application or its job router.
"""
import argparse
import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from functools import partial, wraps
from hashlib import sha256
from ipaddress import ip_address
from pathlib import Path
import re

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from control_center import __version__
from control_center.adapters import catalogue
from control_center.api import routes as readers
from control_center.schemas.responses import Envelope, result
from control_center.storage.files import digest, open_source, read_json
from control_center.storage.index import RunIndex


# Keep native reader imports/decoding on one long-lived worker. In the supplied
# environment Parquet can segfault across successive ASGI worker lifetimes.
# The regression is avoided by keeping native I/O on this worker; scientific
# dependencies remain unchanged. It starts lazily and exits with Python.
READ_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix='mrta-stage1-reader')


def read_endpoint(endpoint):
    @wraps(endpoint)
    async def dispatch(*args, **kwargs):
        return await asyncio.get_running_loop().run_in_executor(READ_WORKER, partial(endpoint, *args, **kwargs))
    return dispatch


class Stage1Index(RunIndex):
    """Gate native table decoding on the manifest before indexing any rows."""

    def inspect_artifact(self, directory, name, cached):
        if not name.startswith(('decisions.', 'outcomes.')):
            return super().inspect_artifact(directory, name, cached)
        manifest_signature = None
        version = None
        try:
            with open_source(self.root, str(Path(directory) / 'run.json')) as (handle, manifest_signature):
                version = read_json(handle).get('schema_version')
        except (HTTPException, ValueError, UnicodeError):
            pass  # Original manifest still receives its own diagnostic below.
        if cached and cached.get('manifest_signature') != manifest_signature:
            cached = None
        if type(version) is int and version == 1:
            item = super().inspect_artifact(directory, name, cached)
        else:
            item = dict(id=name, availability='unsupported', size=None, sha256=None,
                        warning='Missing or unsupported dataset schema version; rows were not decoded')
            try:
                with open_source(self.root, str(Path(directory) / name)) as (handle, sig):
                    if cached and cached.get('signature') == sig:
                        item = dict(cached)
                    else:
                        item.update(signature=sig, size=sig[2], sha256=digest(handle))
            except HTTPException as exc:
                item.update(availability='unavailable', warning=exc.detail)
        item['manifest_signature'] = manifest_signature
        return item


def cli_options(script: Path):
    """Inspect declarations as data; never import or execute a scientific runner."""
    raw = script.read_bytes()
    tree = ast.parse(raw, filename=str(script))
    options = sorted({
        arg.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == 'add_argument'
        for arg in node.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
        and arg.value.startswith('-')
    })
    return dict(options=options, sha256=sha256(raw).hexdigest(),
                source=str(script.relative_to(catalogue.REPO)),
                verification='Source declarations only; no execution or launch support')


async def health():
    return result(dict(status='ok', version=__version__, stage=1, read_only=True))


def capabilities(request: Request):
    data = dict(stage=1, backend_version=__version__, read_only=True, cli={})
    for key, reader in [('strategies', catalogue.strategies), ('scenario_map', catalogue.geometry)]:
        try:
            reader()
            data[key] = dict(implemented=True, readable=True)
        except (ImportError, OSError, ValueError) as exc:
            data[key] = dict(implemented=True, readable=False,
                             reason=f'{type(exc).__name__}: {exc}')
    for key, filename in [('single', 'run_workload.py'), ('experiment', 'run_experiment.py')]:
        try:
            data['cli'][key] = cli_options(catalogue.REPO / 'src/simulation_env/scripts' / filename)
        except (OSError, SyntaxError, ValueError) as exc:
            data['cli'][key] = dict(options=None, reason=f'{type(exc).__name__}: {exc}')
    try:
        import pyarrow.parquet  # noqa: F401 - actual reader dependency probe
        parquet = dict(readable=True)
    except ImportError as exc:
        parquet = dict(readable=False, reason=str(exc))
    data['history'] = dict(implemented=True, readable=request.app.state.index.root.is_dir(),
                           formats={'csv': dict(readable=True), 'parquet': parquet},
                           dataset_versions=[1], metrics_versions=[1, 2])
    for key in ('simulation_launch', 'experiment_control', 'telemetry', 'replay',
                'paired_comparison', 'ilp', 'explanations'):
        data[key] = dict(implemented=False, readable=False, reason='Outside Stage 1')
    return result(data)


def table_page(request, run_id, kind, limit, cursor_value, filters):
    manifest = request.app.state.index.get(run_id)['manifest'] or {}
    version = manifest.get('schema_version')
    if type(version) is not int or version != 1:
        return result(availability='unsupported', warnings=['Missing or unsupported dataset schema version'])
    return readers.table_page(request, run_id, kind, limit, cursor_value, filters)


def decisions(request: Request, run_id: str, limit: int = Query(100, ge=1, le=1000),
              cursor_value: str | None = Query(None, alias='cursor', max_length=2048),
              decision_id: int | None = Query(None, ge=0), order_id: str | None = Query(None, max_length=256),
              robot_id: str | None = Query(None, max_length=256), selected: bool | None = None):
    return table_page(request, run_id, 'decisions', limit, cursor_value,
                      dict(decision_id=decision_id, order_id=order_id, robot_id=robot_id, selected=selected))


def outcomes(request: Request, run_id: str, limit: int = Query(100, ge=1, le=1000),
             cursor_value: str | None = Query(None, alias='cursor', max_length=2048),
             decision_id: int | None = Query(None, ge=0), order_id: str | None = Query(None, max_length=256)):
    return table_page(request, run_id, 'outcomes', limit, cursor_value,
                      dict(decision_id=decision_id, order_id=order_id))


def create_app(*, history_root=None, data_dir=None):
    history_root = Path(history_root) if history_root is not None else catalogue.REPO / 'runs'
    data_dir = Path(data_dir) if data_dir is not None else catalogue.REPO / 'src/dashboard_GUI/var/stage1'

    @asynccontextmanager
    async def lifespan(app):
        app.state.data_dir = data_dir
        app.state.index = Stage1Index(history_root, data_dir)
        yield

    app = FastAPI(title='MRTA Warehouse Control Center — Stage 1', version=__version__, lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({'detail': [{k: e[k] for k in ('loc', 'msg', 'type')} for e in exc.errors()]}, status_code=422)

    @app.exception_handler(ImportError)
    async def unavailable_dependency(request, exc):
        return JSONResponse({'detail': f'Reader dependency unavailable: {exc}'}, status_code=503)

    @app.middleware('http')
    async def local_readonly(request: Request, call_next):
        hosts = request.headers.getlist('host')
        match = re.fullmatch(r'(127\.0\.0\.1|localhost|\[::1\])(?::([0-9]{1,5}))?',
                             hosts[0] if len(hosts) == 1 else '', flags=re.IGNORECASE)
        if not match or (match[2] is not None and not 1 <= int(match[2]) <= 65535):
            return JSONResponse({'detail': 'One local Host required'}, status_code=400)
        try:
            local_peer = request.client is not None and ip_address(request.client.host).is_loopback
        except ValueError:
            local_peer = False
        if not local_peer:
            return JSONResponse({'detail': 'Loopback connection required'}, status_code=403)
        origins = request.headers.getlist('origin')
        if origins and (len(origins) != 1 or origins[0] != f'{request.url.scheme}://{hosts[0]}'):
            return JSONResponse({'detail': 'Same-origin requests only'}, status_code=403)
        if request.method not in ('GET', 'HEAD'):
            return JSONResponse({'detail': 'Stage 1 is read-only'}, status_code=405, headers={'Allow': 'GET, HEAD'})
        if any(scope != 'history' for scope in request.query_params.getlist('scope')):
            return JSONResponse({'detail': 'Stage 1 supports history scope only'}, status_code=422)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    # Explicit allowlist: future additions to the other app's router cannot
    # silently enable job controls in this service.
    for path, endpoint in [
        ('/health', health), ('/capabilities', capabilities),
        ('/strategies', readers.strategies), ('/scenarios', readers.scenarios),
        ('/scenarios/{scenario_id}/map', readers.geometry),
        ('/runs', readers.runs), ('/runs/{run_id}', readers.run),
        ('/runs/{run_id}/metrics', readers.metrics),
        ('/runs/{run_id}/decisions', decisions), ('/runs/{run_id}/outcomes', outcomes),
        ('/runs/{run_id}/artifacts', readers.artifacts),
    ]:
        handler = endpoint if endpoint is health else read_endpoint(endpoint)
        app.get('/api/v1' + path, response_model=Envelope)(handler)
    app.get('/api/v1/runs/{run_id}/artifacts/{artifact_id}')(read_endpoint(readers.download))
    return app


app = create_app()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=args.port, proxy_headers=False, ws='none')


if __name__ == '__main__':
    main()
