"""Explicit local job actions and reconnectable process-status/log transport."""

import asyncio
import secrets

from fastapi.security import APIKeyHeader
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from control_center.schemas.jobs import LaunchRequest, Spec
from control_center.schemas.responses import Envelope, result
from control_center.services.jobs import TERMINAL

router = APIRouter(prefix="/api/v1")
csrf_header = APIKeyHeader(name="X-Control-Center-Token", auto_error=False)


def manager(request):
    return request.app.state.jobs


def mutation(request: Request, token: str | None = Depends(csrf_header)):
    expected = manager(request).token
    actual = token or ""
    if not secrets.compare_digest(actual.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(
            403, "Obtain /api/v1/session and supply X-Control-Center-Token"
        )
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(415, "JSON content type required")


@router.get("/session", response_model=Envelope)
def session(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        "control_center_session",
        manager(request).token,
        httponly=True,
        samesite="strict",
        path="/api/v1",
    )
    return result(dict(csrf_token=manager(request).token))


@router.post("/jobs/preview", response_model=Envelope, dependencies=[Depends(mutation)])
def preview(request: Request, spec: Spec):
    from control_center.api.routes import source_call

    return result(source_call(lambda: manager(request).preview(spec)))


@router.post(
    "/runs", response_model=Envelope, status_code=202, dependencies=[Depends(mutation)]
)
def launch_run(request: Request, body: LaunchRequest):
    return result(manager(request).launch(body.preview_id, "run"))


@router.post(
    "/experiments",
    response_model=Envelope,
    status_code=202,
    dependencies=[Depends(mutation)],
)
def launch_experiment(request: Request, body: LaunchRequest):
    return result(manager(request).launch(body.preview_id, "experiment"))


@router.post(
    "/gazebo",
    response_model=Envelope,
    status_code=202,
    dependencies=[Depends(mutation)],
)
def launch_gazebo(request: Request, body: LaunchRequest):
    return result(manager(request).launch(body.preview_id, "gazebo"))


@router.get("/jobs", response_model=Envelope)
def jobs(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
):
    return result(manager(request).list(offset, limit))


@router.get("/jobs/{job_id}", response_model=Envelope)
def job(request: Request, job_id: str):
    return result(manager(request).get(job_id))


@router.get("/jobs/{job_id}/snapshot", response_model=Envelope)
def snapshot(request: Request, job_id: str):
    job = manager(request).get(job_id)
    return result(
        job,
        warnings=[]
        if job["telemetry"]["available"]
        else [job["telemetry"].get("reason", "Fleet observation pending/unavailable")],
    )


@router.get("/jobs/{job_id}/telemetry", response_model=Envelope)
def telemetry(
    request: Request,
    job_id: str,
    cursor: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
):
    page = manager(request).telemetry(job_id, cursor, limit)
    return result(page, availability="available" if page is not None else "unavailable")


@router.get("/runs/{run_id}/telemetry", response_model=Envelope)
def run_telemetry(
    request: Request,
    run_id: str,
    cursor: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
):
    from control_center.api.routes import index

    record = index(request).get(run_id)
    directory = index(request).root / record["directory"]
    jid = manager(request).observed_job_for_output(directory)
    if jid is not None:
        page = manager(request).telemetry(jid, cursor, limit)
        return result(
            page, availability="available" if page is not None else "unavailable"
        )
    return result(
        availability="unavailable",
        warnings=["No genuine fleet recording is associated with this run"],
    )


@router.get("/jobs/{job_id}/logs", response_model=Envelope)
def logs(
    request: Request,
    job_id: str,
    cursor: int = Query(0, ge=0),
    limit: int = Query(65536, ge=1, le=65536),
):
    return result(manager(request).logs(job_id, cursor, limit))


@router.post(
    "/jobs/{job_id}/cancel", response_model=Envelope, dependencies=[Depends(mutation)]
)
def cancel(request: Request, job_id: str):
    return result(manager(request).cancel(job_id))


@router.websocket("/ws/jobs/{job_id}")
async def stream(
    websocket: WebSocket, job_id: str, after_sequence: int = Query(0, ge=0)
):
    from control_center.app import local_violation

    expected = websocket.app.state.jobs.token
    actual = websocket.cookies.get("control_center_session", "")
    if local_violation(websocket, require_origin=True) or not secrets.compare_digest(
        actual.encode("utf-8"), expected.encode("utf-8")
    ):
        await websocket.close(code=1008)
        return
    m = websocket.app.state.jobs
    try:
        await asyncio.to_thread(m.get, job_id)
    except HTTPException:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        while True:
            batch = await asyncio.to_thread(m.events, job_id, after_sequence)
            if batch["resync_required"]:
                latest = await asyncio.to_thread(m.get, job_id)
                await websocket.send_json(
                    dict(
                        type="resync_required",
                        snapshot=latest,
                        sequence=latest["sequence"],
                        log_recovery="Use byte-cursor logs endpoint",
                    )
                )
                after_sequence = latest["sequence"]
            else:
                for event in batch["events"]:
                    await websocket.send_json(event)
                    after_sequence = event["sequence"]
            latest = await asyncio.to_thread(m.get, job_id)
            if (
                latest["status"] in TERMINAL
                and not latest["owned_process_alive"]
                and after_sequence >= latest["sequence"]
            ):
                await websocket.close(code=1000)
                return
            # Wait on disconnect or a short timeout, without blocking the ASGI loop.
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=0.25)
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        pass
    except HTTPException:
        await websocket.close(code=1008)
