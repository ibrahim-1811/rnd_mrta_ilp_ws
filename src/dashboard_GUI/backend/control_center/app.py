"""Local ASGI application with explicit owned headless job actions."""

from contextlib import asynccontextmanager
from ipaddress import ip_address
from pathlib import Path
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from control_center import __version__
from control_center.adapters.catalogue import REPO
from control_center.api.routes import router
from control_center.api.jobs import router as jobs_router
from control_center.api.research import router as research_router
from control_center.services.jobs import JobManager
from control_center.storage.index import RunIndex


def local_authority(value):
    match = re.fullmatch(
        r"(127\.0\.0\.1|localhost|\[::1\])(?::([0-9]{1,5}))?", value, re.IGNORECASE
    )
    return bool(match and (match[2] is None or 1 <= int(match[2]) <= 65535))


def local_violation(connection, *, require_origin=False):
    hosts = connection.headers.getlist("host")
    if len(hosts) != 1 or not local_authority(hosts[0]):
        return 400, "One local Host required"
    try:
        peer_is_local = (
            connection.client is not None
            and ip_address(connection.client.host).is_loopback
        )
    except ValueError:
        peer_is_local = False
    if not peer_is_local:
        return 403, "Loopback connection required"
    origins = connection.headers.getlist("origin")
    scheme = "https" if connection.url.scheme in ("https", "wss") else "http"
    if (require_origin or origins) and (
        len(origins) != 1 or origins[0] != f"{scheme}://{hosts[0]}"
    ):
        return 403, "Same-origin requests only"
    return None


def create_app(*, history_root=None, data_dir=None):
    history_root = Path(history_root) if history_root is not None else REPO / "runs"
    data_dir = (
        Path(data_dir) if data_dir is not None else REPO / "src/dashboard_GUI/var"
    )

    @asynccontextmanager
    async def lifespan(app):
        app.state.data_dir = data_dir
        app.state.index = RunIndex(history_root, data_dir)
        app.state.app_index = RunIndex(data_dir / "runs", data_dir / "app-index")
        app.state.jobs = JobManager(data_dir)
        try:
            yield
        finally:
            import asyncio

            await asyncio.to_thread(app.state.jobs.close)

    app = FastAPI(
        title="MRTA Warehouse Control Center", version=__version__, lifespan=lifespan
    )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Do not echo nonfinite input values or unserializable validator context.
        return JSONResponse(
            {
                "detail": [
                    {k: e[k] for k in ("loc", "msg", "type")} for e in exc.errors()
                ]
            },
            status_code=422,
        )

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        violation = local_violation(request)
        if violation:
            return JSONResponse({"detail": violation[1]}, status_code=violation[0])
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    app.include_router(router)
    app.include_router(jobs_router)
    app.include_router(research_router)
    return app


app = create_app()


def main():
    import uvicorn

    uvicorn.run(
        "control_center.app:app", host="127.0.0.1", port=8000, proxy_headers=False
    )


if __name__ == "__main__":
    main()
