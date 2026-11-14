"""Tiny Stage 3 observed-app/CLI parity and real local HTTP/WebSocket check."""

from fractions import Fraction
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
from control_center.schemas.jobs import RunSpec
from control_center.services.jobs import JobManager, TERMINAL


def main():
    root = (
        REPO
        / "src/dashboard_GUI/var/verification"
        / ("stage3_" + uuid.uuid4().hex[:10])
    )
    with JobManager(root) as manager:
        plan = manager.preview(
            RunSpec(
                execution_mode="observed",
                robots=2,
                orders=3,
                seed=7,
                strategy="random",
                quantum_s=3,
                progress_interval_s=0,
                export=True,
            )
        )
        job = manager.launch(plan["preview_id"], "run")
        deadline = time.monotonic() + 20
        while job["status"] not in TERMINAL:
            assert time.monotonic() < deadline, job
            time.sleep(0.05)
            job = manager.get(job["id"])
        assert job["status"] == "completed", job
        assert (
            job["telemetry"]["available"]
            and job["telemetry"]["footer"]["scientific_run_returned"]
        ), job["telemetry"]
        assert job["telemetry"]["availability"] in ("available", "partial")
        assert job["snapshot"]["payload"]["orders"]["completed"] == 3
        cli = root / "cli_parity"
        argv = list(plan["original_cli_argv"])
        argv[argv.index("--dataset") + 1] = str(cli)
        with (root / "cli.log").open("wb") as log:
            done = subprocess.run(
                argv,
                cwd=REPO,
                env=manager.planner.environment(),
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=20,
            )
        assert done.returncode == 0
        from simulation_env.dataset_export import read_run

        actual = Path(job["output_dir"])
        assert read_run(actual) == read_run(cli)
        host = {
            "wall_clock_s",
            "allocator_cpu_s",
            "mission_build_cpu_s",
            "worst_dispatch_s",
            "allocator_decision_median_s",
            "allocator_decision_worst_s",
        }
        metrics = [json.loads((p / "metrics.json").read_text()) for p in (actual, cli)]
        assert {k: v for k, v in metrics[0].items() if k not in host} == {
            k: v for k, v in metrics[1].items() if k not in host
        }
        assert (
            float(Fraction(job["snapshot"]["sim_time_exact"]))
            == metrics[0]["makespan_s"]
        )
        for name in ("decisions.csv", "outcomes.csv", "bk.pl", "exs.pl"):
            assert (actual / name).read_bytes() == (cli / name).read_bytes()
        trace = root / "jobs" / job["id"] / "telemetry.ndjson"
        rows = [json.loads(line) for line in trace.read_text().splitlines()]
        samples = [r for r in rows if r["type"] == "fleet_snapshot"]
        result = dict(
            output_root=str(root),
            job_id=job["id"],
            samples=len(samples),
            first_time_exact=samples[0]["sim_time_exact"],
            final_time_exact=samples[-1]["sim_time_exact"],
            final_robot_cells={
                r["robot_id"]: r["cell"] for r in samples[-1]["payload"]["robots"]
            },
            dropped=rows[-1]["payload"]["dropped_samples_total"],
            deterministic_metric_keys=len(set(metrics[0]) - host),
            scientific_records_equal=True,
            host_timing_observed={k: metrics[0][k] for k in host},
            host_timing_cli={k: metrics[1][k] for k in host},
        )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    code = (
        "from control_center.app import create_app;import uvicorn;"
        f'uvicorn.run(create_app(data_dir={str(root)!r}),host="127.0.0.1",port={port},proxy_headers=False)'
    )
    with (root / "socket.log").open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-c", code], cwd=REPO, stdout=log, stderr=subprocess.STDOUT
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}") as client:
                deadline = time.monotonic() + 10
                while True:
                    try:
                        if client.get("/api/v1/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert time.monotonic() < deadline, (
                        root / "socket.log"
                    ).read_text()
                    time.sleep(0.05)
                client.get("/api/v1/session").raise_for_status()
                data = client.get(f"/api/v1/jobs/{job['id']}/snapshot").json()["data"]
                assert (
                    data["snapshot"] == job["snapshot"]
                    and data["telemetry"]["available"]
                )
                cookie = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
                with connect(
                    f"ws://127.0.0.1:{port}/api/v1/ws/jobs/{job['id']}",
                    origin=f"http://127.0.0.1:{port}",
                    additional_headers={"Cookie": cookie},
                ) as ws:
                    for _ in range(1000):
                        event = json.loads(ws.recv(timeout=3))
                        if event["type"] == "fleet_snapshot":
                            assert (
                                event["source"] == "executor_observer"
                                and event["sim_time_exact"] is not None
                            )
                            result["websocket_snapshot_sequence"] = event["sequence"]
                            break
                        if event["type"] == "resync_required":
                            assert (
                                event["snapshot"]["snapshot"]["source"]
                                == "executor_observer"
                            )
                            result["websocket_resync_sequence"] = event["sequence"]
                            break
                    else:
                        raise AssertionError(
                            "No genuine snapshot delivered over WebSocket"
                        )
                page = client.get(f"/api/v1/jobs/{job['id']}/telemetry?limit=1").json()[
                    "data"
                ]
                assert page["records"][0]["type"] == "telemetry_header"
                result["socket_health"] = 200
        finally:
            process.terminate()
            process.wait(timeout=10)
    (root / "result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    print(
        "PASS: observed app, original CLI, artifacts, exact time, HTTP snapshot and real WebSocket"
    )


if __name__ == "__main__":
    main()
