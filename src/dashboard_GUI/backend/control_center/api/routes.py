import csv
from pathlib import Path
import shutil
import tempfile

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from control_center import __version__
from control_center.adapters import catalogue
from control_center.adapters.scenarios import InvalidScenario, UnknownScenario, UnsafeScenarioId
from control_center.adapters.tables import rows
from control_center.schemas.responses import Envelope, result
from control_center.services.pagination import cursor, fingerprint, offset
from control_center.storage.files import ARTIFACTS, open_source, read_json
from control_center.storage.index import metric_availability

router = APIRouter(prefix='/api/v1')


def source_call(function):
    try:
        return function()
    except (ImportError, OSError, ValueError) as exc:
        raise HTTPException(503, f'Production source unavailable: {type(exc).__name__}: {exc}') from exc


def index(request):
    scope = request.query_params.get('scope', 'history')
    if scope not in ('history', 'app'):
        raise HTTPException(422, 'scope must be history or app')
    return request.app.state.app_index if scope == 'app' else request.app.state.index


def summary(record):
    return {k: v for k, v in record.items() if k not in ('artifacts', 'manifest')}


@router.get('/health', response_model=Envelope)
def health():
    return result(dict(status='ok', version=__version__, read_only=False))


@router.get('/capabilities', response_model=Envelope)
def capabilities(request: Request):
    data = {}
    for key, reader in [('strategies', catalogue.strategies), ('scenario_map', catalogue.geometry)]:
        try:
            reader()
            data[key] = dict(implemented=True, readable=True)
        except (ImportError, OSError, ValueError) as exc:
            data[key] = dict(implemented=True, readable=False, reason=str(exc))
    data['history'] = dict(implemented=True, readable=index(request).root.is_dir(),
                           formats=['csv', 'parquet'], metrics_versions=[1, 2], dataset_versions=[1])
    data['simulation_launch'] = dict(implemented=True, readable=True, requires_session=True)
    data['experiment_control'] = dict(implemented=True, readable=True, requires_session=True,
                                      scope='Original CLI matrix controls; guarded paired analysis')
    from control_center.adapters.observed_runner import source_matches
    supported = source_matches()
    data['telemetry'] = dict(implemented=True, readable=supported, scope='Explicit observed single-run mode only',
                             reason=None if supported else 'Original runner changed; original CLI mode remains available')
    data['paired_comparison'] = dict(implemented=True, readable=True, scope='Seeded experiments, validated original per-run metrics')
    data['csv_inputs'] = dict(implemented=True, readable=True, scope='Single runs only; exact schema; 2 MiB / 50000 line items')
    for key in ('replay', 'ilp', 'explanations'):
        data[key] = dict(implemented=False, readable=False, planned=True, reason='Outside Stage 2')
    data['backend_version'] = __version__
    return result(data)


@router.get('/strategies', response_model=Envelope)
def strategies():
    return result(source_call(catalogue.strategies), source={'registry': 'task_management.allocation_strategy.BATCH_STRATEGIES'})


@router.get('/scenarios', response_model=Envelope)
def scenarios():
    return result(source_call(catalogue.scenarios))


@router.get('/scenarios/{scenario_id}/map', response_model=Envelope)
def geometry(scenario_id: str):
    try:
        return result(catalogue.geometry(scenario_id))
    except UnsafeScenarioId as exc:
        raise HTTPException(422, str(exc)) from exc
    except UnknownScenario as exc:
        raise HTTPException(404, str(exc)) from exc
    except InvalidScenario as exc:
        raise HTTPException(422, str(exc)) from exc
    except (ImportError, OSError, ValueError) as exc:
        raise HTTPException(503, f'Production source unavailable: {type(exc).__name__}: {exc}') from exc


@router.get('/runs', response_model=Envelope)
def runs(request: Request, limit: int = Query(100, ge=1, le=1000),
         cursor_value: str | None = Query(None, alias='cursor', max_length=2048),
         strategy: str | None = Query(None, max_length=100),
         integrity: str | None = Query(None, pattern='^(complete|incomplete)$')):
    records = index(request).rebuild()
    records = [r for r in records if (strategy is None or (r['manifest'] or {}).get('strategy') == strategy)
               and (integrity is None or r['integrity'] == integrity)]
    identity = fingerprint([records, strategy, integrity])
    start = offset(cursor_value, identity)
    page = [summary(r) for r in records[start:start + limit]]
    next_page = cursor(identity, start + limit) if start + limit < len(records) else None
    return result(page, next_cursor=next_page)


@router.get('/runs/{run_id}', response_model=Envelope)
def run(request: Request, run_id: str):
    record = index(request).get(run_id)
    # Reopen the authoritative manifest, never use the SQLite value as science.
    try:
        with open_source(index(request).root, str(Path(record['directory']) / 'run.json')) as (handle, _):
            manifest = read_json(handle)
    except (HTTPException, ValueError, UnicodeError):
        manifest = None
    return result(dict(manifest=manifest, operational=summary(record)), warnings=record['warnings'])


@router.get('/runs/{run_id}/metrics', response_model=Envelope)
def metrics(request: Request, run_id: str):
    record = index(request).get(run_id)
    try:
        with open_source(index(request).root, str(Path(record['directory']) / 'metrics.json')) as (handle, sig):
            values = read_json(handle)
        return result(values, availability=metric_availability(values), warnings=record['warnings'],
                      source=dict(artifact='metrics.json', signature=sig,
                                  units_reference='docs/EXPERIMENT_PROTOCOL.md#run-metrics-schema'))
    except HTTPException as exc:
        if exc.status_code == 409:
            raise
        return result(availability='missing', warnings=[exc.detail])
    except (ValueError, UnicodeError) as exc:
        return result(availability='invalid', warnings=[str(exc)[:200]])


def table_page(request, run_id, kind, limit, cursor_value, filters):
    record = index(request).get(run_id)
    manifest = record['manifest'] or {}
    fmt = manifest.get('format')
    if fmt not in ('csv', 'parquet'):
        return result(availability='unsupported', warnings=['Manifest does not identify a supported format'])
    name = f'{kind}.{fmt}'
    filters = {k: v for k, v in filters.items() if v is not None}
    try:
        with open_source(index(request).root, str(Path(record['directory']) / name)) as (handle, sig):
            identity = fingerprint([run_id, name, sig, filters])
            start = offset(cursor_value, identity)
            page = []
            matched = 0
            more = False
            for row in rows(handle, name):
                if not all(row.get(k) == v for k, v in filters.items()):
                    continue
                if matched >= start:
                    if len(page) == limit:
                        more = True
                        break
                    page.append(row)
                matched += 1
        return result(page, next_cursor=cursor(identity, start + len(page)) if more else None,
                      source=dict(artifact=name, signature=sig, ordering='physical row order'),
                      warnings=record['warnings'])
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return result(availability='missing', warnings=[exc.detail])
    except (ValueError, UnicodeError, OSError, csv.Error) as exc:
        return result(availability='invalid', warnings=[str(exc)[:200]])


@router.get('/runs/{run_id}/decisions', response_model=Envelope)
def decisions(request: Request, run_id: str, limit: int = Query(100, ge=1, le=1000),
              cursor_value: str | None = Query(None, alias='cursor', max_length=2048),
              decision_id: int | None = Query(None, ge=0), order_id: str | None = Query(None, max_length=256),
              robot_id: str | None = Query(None, max_length=256), selected: bool | None = None):
    return table_page(request, run_id, 'decisions', limit, cursor_value,
                      dict(decision_id=decision_id, order_id=order_id, robot_id=robot_id, selected=selected))


@router.get('/runs/{run_id}/outcomes', response_model=Envelope)
def outcomes(request: Request, run_id: str, limit: int = Query(100, ge=1, le=1000),
             cursor_value: str | None = Query(None, alias='cursor', max_length=2048),
             decision_id: int | None = Query(None, ge=0), order_id: str | None = Query(None, max_length=256)):
    return table_page(request, run_id, 'outcomes', limit, cursor_value,
                      dict(decision_id=decision_id, order_id=order_id))


@router.get('/runs/{run_id}/artifacts', response_model=Envelope)
def artifacts(request: Request, run_id: str):
    record = index(request).get(run_id)
    return result([{k: v for k, v in a.items() if k not in ('metadata', 'signature')}
                   for a in record['artifacts']])


@router.get('/runs/{run_id}/artifacts/{artifact_id}')
def download(request: Request, run_id: str, artifact_id: str):
    if artifact_id not in ARTIFACTS:
        raise HTTPException(404, 'Unknown artifact')
    record = index(request).get(run_id)
    spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, dir=request.app.state.data_dir)
    try:
        with open_source(index(request).root, str(Path(record['directory']) / artifact_id)) as (handle, _):
            shutil.copyfileobj(handle, spool, length=1024 * 1024)
        spool.seek(0)
    except BaseException:
        spool.close()
        raise
    def chunks():
        try:
            while block := spool.read(1024 * 1024):
                yield block
        finally:
            spool.close()
    return StreamingResponse(chunks(), media_type='application/octet-stream',
                             headers={'Content-Disposition': f'attachment; filename="{artifact_id}"',
                                      'X-Content-Type-Options': 'nosniff'})
