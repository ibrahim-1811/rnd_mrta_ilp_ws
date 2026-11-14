"""One owned headless process group, original CLI, durable status and logs."""

from datetime import datetime, timezone
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import threading
import time
import uuid

from fastapi import HTTPException
from control_center.adapters.commands import CommandPlanner, fingerprint
from control_center.schemas.jobs import ExperimentSpec, GazeboSpec, RunSpec
from control_center.services.processes import (
    identity,
    members,
    signal_group,
    stop_group,
)
from control_center.storage.files import open_source, read_json
from control_center.storage.index import RunIndex
from control_center.storage.jobs import JobStore, owned_directory

ACTIVE = {"queued", "starting", "running", "cancel_requested"}
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def now():
    return datetime.now(timezone.utc).isoformat()


class JobManager:
    def __init__(
        self, data_dir, *, planner=None, grace_s=2.0, max_log_bytes=64 * 1024 * 1024
    ):
        # Initialize the native reader on the lifespan/owner thread, before
        # short-lived worker threads: lazy first import there crashes PyArrow
        # in this environment after worker-thread reuse (regression tested).
        try:
            import pyarrow.parquet  # noqa: F401
        except ImportError:
            pass  # CSV-only history remains usable.
        self.root = owned_directory(data_dir)
        fd = os.open(
            self.root / "jobs.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise RuntimeError(
                "Another Control Center owns this job directory"
            ) from exc
        self.lock_fd = fd
        self.store = JobStore(self.root)
        self.planner = planner or CommandPlanner(self.root)
        self.lock = threading.RLock()
        self.grace_s = grace_s
        self.max_log_bytes = max_log_bytes
        self.token = secrets.token_urlsafe(32)
        self.previews = {}
        self.workers = {}
        self.closed = False
        for job in self.store.all():
            if job["status"] in ACTIVE:
                job.update(
                    status="interrupted",
                    error="Backend restarted; process exit is unknown",
                    ended_at=now(),
                    exit_code=None,
                )
                self._emit(job)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _emit(self, job):
        job["updated_at"] = now()
        self.store.event(
            job,
            "job_status",
            dict(
                status=job["status"],
                exit_code=job.get("exit_code"),
                artifact_status=job.get("artifact_status"),
                error=job.get("error"),
            ),
            now(),
        )

    def _recover_identity(self, job):
        if job.get("process_identity") is None and job.get("launch_nonce"):
            try:
                with open_source(
                    self.root, f"jobs/{job['id']}/process_identity.json"
                ) as (h, _):
                    ready = read_json(h)
                if ready.pop("nonce", None) == job["launch_nonce"] and members(ready):
                    job["process_identity"] = ready
                    self.store.save(job)
            except (HTTPException, ValueError, KeyError, TypeError):
                pass
        self._remember_experiment_owner(job)
        return job

    def _remember_experiment_owner(self, job):
        """Associate provenance with observed output, never with an attempted force."""
        if (
            job.get("kind") != "experiment"
            or not job.get("ownership_pending")
            or job.get("status") not in TERMINAL
            or members(job.get("process_identity"))
        ):
            return
        plan = job["plan"]
        request = plan["request"]
        output = Path(job["output_dir"])
        try:
            if not job.get("started_at") or request["mode"] == "resume":
                return
            if output.is_symlink() or not output.is_dir():
                return
            st = output.stat()
            generation = [st.st_dev, st.st_ino]
            if request["mode"] == "force" and generation == plan.get(
                "output_identity_before"
            ):
                return  # The original runner has not replaced the old directory.
            with open_source(output, "experiment.json") as (handle, _):
                manifest = read_json(handle)
            if (
                manifest.get("spec") != plan["effective"]["spec"]
                or manifest.get("runs") != plan["grid"]
            ):
                return
            owners = owned_directory(self.root / "experiment_owners")
            name = request["name"] + ".json"
            try:
                with open_source(owners, name) as (handle, _):
                    previous = read_json(handle)
                if (
                    previous.get("output_identity") == generation
                    and previous.get("inputs_hash") == plan["inputs_hash"]
                ):
                    return
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
            record = dict(
                inputs_hash=plan["inputs_hash"],
                job_id=job["id"],
                output_identity=generation,
            )
            temporary = owners / (name + "." + uuid.uuid4().hex + ".tmp")
            try:
                with temporary.open("x") as handle:
                    json.dump(record, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, owners / name)
            finally:
                temporary.unlink(missing_ok=True)
        except (HTTPException, OSError, ValueError):
            # An absent or unstable manifest cannot establish output ownership.
            # Leave the old owner intact when replacement evidence is unavailable.
            return
        finally:
            # This attempt has ended. It must never attribute a future job's
            # replacement directory to its own source fingerprint.
            job["ownership_pending"] = False
            self.store.save(job)

    def _get(self, jid):
        job = self.store.get(jid)
        if job is None:
            raise HTTPException(404, "Unknown app-owned job")
        job = self._recover_identity(job)
        self._observe(job)
        return job

    def _observe(self, job):
        if job.get("plan", {}).get("execution_mode") != "observed":
            return False
        before = deepcopy(job)
        try:
            from control_center.services.telemetry import ingest

            return ingest(self, job)
        except Exception as exc:
            # No optional import/relay/error-reporting exception may enter the
            # core monitor exception handler, which terminates owned processes.
            try:
                committed = self.store.get(job["id"]) or before
            except Exception:
                committed = before
            job.clear()
            job.update(committed)
            job.setdefault("telemetry", {}).update(
                available=False,
                availability="invalid",
                recording_complete=False,
                reason=f"Optional observation unavailable: {type(exc).__name__}: {exc}"[
                    :300
                ],
            )
            try:
                self.store.save(job)
            except Exception:
                pass
            return False

    def get(self, jid):
        with self.lock:
            job = self._get(jid)
            job["owned_process_alive"] = bool(members(job.get("process_identity")))
            if job.get("started_at"):
                end = job.get("ended_at") or now()
                job["elapsed_wall_s"] = max(
                    0,
                    (
                        datetime.fromisoformat(end)
                        - datetime.fromisoformat(job["started_at"])
                    ).total_seconds(),
                )
            if job.get("plan", {}).get("execution_mode") != "observed":
                job["telemetry"] = dict(
                    available=False,
                    availability="unavailable",
                    reason="Original CLI mode provides status and logs only",
                )
            elif job.get("snapshot"):
                age = max(
                    0,
                    (
                        datetime.now(timezone.utc)
                        - datetime.fromisoformat(job["snapshot"]["observed_at_utc"])
                    ).total_seconds(),
                )
                job["telemetry"].update(
                    age_wall_s=age,
                    stale=age > 5 or not job["telemetry"]["available"],
                    live=bool(
                        job["status"] in ACTIVE
                        and job["owned_process_alive"]
                        and job["telemetry"]["available"]
                        and not job["telemetry"].get("footer")
                    ),
                )
            return job

    def list(self, offset=0, limit=100):
        with self.lock:
            return [
                self.get(j["id"]) for j in self.store.all()[offset : offset + limit]
            ]

    def preview(self, spec):
        with self.lock:
            if self.closed:
                raise HTTPException(503, "Job manager is closing")
            self.previews = {
                k: v for k, v in self.previews.items() if v[0] > time.monotonic()
            }
            if len(self.previews) >= 128:
                raise HTTPException(429, "Preview capacity reached; wait for expiry")
            jid, pid = uuid.uuid4().hex, uuid.uuid4().hex
            plan = self.planner.build(spec, jid)
            self.previews[pid] = (time.monotonic() + 600, spec, plan)
            return dict(preview_id=pid, expires_in_s=600, **plan)

    def launch(self, preview_id, kind):
        with self.lock:
            if self.closed:
                raise HTTPException(503, "Job manager is closing")
            value = self.previews.get(preview_id)
            if not value or value[0] <= time.monotonic():
                raise HTTPException(
                    409, "Preview expired, used or unknown; preview again"
                )
            _, spec, plan = value
            if plan["kind"] != kind:
                raise HTTPException(409, "Preview kind does not match launch route")
            all_jobs = [self._recover_identity(j) for j in self.store.all()]
            if any(
                j["status"] in ACTIVE
                or members(j.get("process_identity"))
                or (
                    j.get("launch_uncertain_until", "") > now()
                    and j.get("process_identity") is None
                )
                for j in all_jobs
            ):
                raise HTTPException(
                    409,
                    "An active or interrupted owned process must settle or be cancelled first",
                )
            current = self.planner.build(spec, plan["job_id"])
            if current["fingerprint"] != plan["fingerprint"]:
                raise HTTPException(
                    409, "Inputs/output changed after preview; preview again"
                )
            del self.previews[preview_id]
            output = Path(plan["output_dir"])
            # Never create a writable output via an ancestor symlink.
            owned_directory(output.parent)
            job_dir = owned_directory(self.root / "jobs" / plan["job_id"])
            job = dict(
                id=plan["job_id"],
                kind=kind,
                status="queued",
                sequence=0,
                created_at=now(),
                started_at=None,
                ended_at=None,
                exit_code=None,
                process_identity=None,
                artifact_status="pending",
                error=None,
                log_path=str(job_dir / "process.log"),
                output_dir=str(output),
                plan=plan,
                artifact_run_ids=[],
                launch_nonce=secrets.token_hex(16),
                ownership_pending=kind == "experiment",
                launch_uncertain_until=datetime.fromtimestamp(
                    time.time() + 15, timezone.utc
                ).isoformat(),
            )
            self._emit(job)
            self._emit_status(job, "starting")
            process = None
            try:
                if isinstance(spec, ExperimentSpec):
                    if spec.mode != "new":
                        shutil.copytree(
                            output, job_dir / "previous_attempt", symlinks=False
                        )
                with open(job["log_path"], "xb") as log:
                    launcher = Path(__file__).with_name("job_gate.py")
                    job["launch_argv"] = [
                        plan["argv"][0],
                        str(launcher),
                        str(job_dir),
                        job["launch_nonce"],
                        *plan["argv"],
                    ]
                    process = subprocess.Popen(
                        job["launch_argv"],
                        cwd=plan["cwd"],
                        env=self.planner.environment(),
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        shell=False,
                        start_new_session=True,
                    )
                job.update(started_at=now(), process_identity=identity(process.pid))
                if job["process_identity"] is None:
                    # Very short processes may already have exited; no unknown PID is ever signalled.
                    job["error"] = (
                        "Process identity unavailable; no later process control is possible"
                    )
                self._emit_status(job, "running")
                # Durable DB ownership precedes authorization; helper never execs before this.
                with (job_dir / "exec_authorized").open("x") as permit:
                    permit.write(job["launch_nonce"])
                    permit.flush()
                    os.fsync(permit.fileno())
                thread = threading.Thread(
                    target=self._monitor,
                    args=(job["id"], process),
                    daemon=True,
                    name=f"job-{job['id'][:8]}",
                )
                self.workers[job["id"]] = thread
                thread.start()
            except Exception as exc:
                if process is not None:
                    stop_group(
                        job.get("process_identity") or identity(process.pid),
                        process,
                        self.grace_s,
                    )
                job.update(
                    error=f"Launch failed: {type(exc).__name__}: {exc}", ended_at=now()
                )
                self._emit_status(job, "failed")
            return self.get(job["id"])

    def _emit_status(self, job, status):
        job["status"] = status
        self._remember_experiment_owner(job)
        self._emit(job)

    def _harvest(self, job, offset):
        with open_source(
            self.root, str(Path(job["log_path"]).relative_to(self.root))
        ) as (h, _):
            h.seek(offset)
            data = h.read(65536)
        # Log is growing by design; stable-read detection may ask for a retry.
        for start in range(0, len(data), 4096):
            block = data[start : start + 4096]
            self.store.event(
                job,
                "log",
                dict(
                    text=block.decode("utf-8", errors="replace"),
                    byte_cursor=offset + start + len(block),
                ),
                now(),
            )
        return offset + len(data)

    def _validate(self, job):
        plan = job["plan"]
        output = Path(job["output_dir"])
        idx = RunIndex(output, self.root / "validation" / job["id"])
        records = idx.rebuild()
        valid = [
            r
            for r in records
            if r["integrity"] == "complete" and r["scientific_status"] == "completed"
        ]
        if job["kind"] in ("run", "gazebo"):
            expected = plan["effective"]["orders"]
            ok = (
                len(records) == len(valid) == 1
                and valid[0]["manifest"].get("orders") == expected
            )
        else:
            want = {tuple(x) for x in plan["grid"]}
            have = {
                (
                    r["manifest"].get("strategy"),
                    r["manifest"].get("robot_count"),
                    r["manifest"].get("seed"),
                )
                for r in valid
            }
            ok = len(records) == len(valid) == len(want) and have == want
        if ok:
            from control_center.adapters.catalogue import load_scenario
            from control_center.adapters.validation import validate_manifest

            config, warehouse = load_scenario()
            spec_type = {
                "run": RunSpec,
                "experiment": ExperimentSpec,
                "gazebo": GazeboSpec,
            }[job["kind"]]
            spec = spec_type.model_validate(plan["request"])
            if isinstance(spec, RunSpec) and spec.input_id:
                from dataclasses import replace
                from control_center.adapters.inputs import InputStore

                config = replace(
                    config, orders_path=InputStore(self.root).resolve(spec.input_id)
                )
            if job["kind"] == "gazebo":
                from control_center.adapters.gazebo import GazeboPlanner

                sources = GazeboPlanner(self.root).inputs()
            else:
                sources = (
                    self.planner.inputs(config, observed=True)
                    if plan.get("execution_mode") == "observed"
                    else self.planner.inputs(config)
                )
            if fingerprint(sources) != plan["inputs_hash"]:
                raise ValueError(
                    "Source/input fingerprint changed during execution; requested provenance is unverified"
                )
            for record in valid:
                manifest = record["manifest"]
                if job["kind"] in ("run", "gazebo"):
                    strategy, robots, seed = (
                        spec.strategy,
                        plan["effective"]["robots"],
                        plan["effective"]["seed"],
                    )
                else:
                    strategy, robots, seed = (
                        manifest["strategy"],
                        manifest["robot_count"],
                        manifest["seed"],
                    )
                validate_manifest(
                    record, spec, config, warehouse, output, strategy, robots, seed
                )
        return ("complete" if ok else "incomplete"), [r["id"] for r in records]

    def _monitor(self, jid, process):
        offset, cancel_since = 0, None
        try:
            while True:
                with self.lock:
                    job = self._get(jid)
                    try:
                        offset = self._harvest(job, offset)
                    except HTTPException as exc:
                        if exc.status_code != 409:
                            raise
                    log_size = Path(job["log_path"]).stat().st_size
                    if (
                        log_size > self.max_log_bytes
                        and job["status"] != "cancel_requested"
                    ):
                        job["error"] = (
                            "Operational log limit exceeded; cancelling owned job"
                        )
                        self._emit_status(job, "cancel_requested")
                    cancelling = job["status"] == "cancel_requested"
                    code = process.poll()
                    alive = members(job.get("process_identity"))
                    # If the leader exits with descendants, do not leak children or release the slot.
                    if cancelling or (code is not None and alive):
                        cancel_since = cancel_since or time.monotonic()
                        signal_group(
                            job.get("process_identity"),
                            force=time.monotonic() - cancel_since >= self.grace_s,
                        )
                    if code is not None and not alive:
                        while self._observe(job):
                            pass  # Child has exited; drain bounded recording pages.
                        # Drain remaining log bytes into bounded event storage before terminal status.
                        while offset < log_size:
                            offset = self._harvest(job, offset)
                        job["exit_code"] = code
                        job["ended_at"] = now()
                        if cancelling:
                            job["artifact_status"] = "partial"
                            self._emit_status(job, "cancelled")
                        else:
                            try:
                                job["artifact_status"], job["artifact_run_ids"] = (
                                    self._validate(job)
                                )
                            except Exception as exc:
                                job["artifact_status"] = "invalid"
                                job["error"] = (
                                    f"Artifact validation failed: {type(exc).__name__}: {exc}"
                                )
                            if code != 0:
                                job["error"] = (
                                    job.get("error")
                                    or f"Runner exited with code {code}; inspect original logs"
                                )
                            elif job["artifact_status"] != "complete":
                                job["error"] = (
                                    job.get("error")
                                    or "Runner exited 0 but expected artifacts are incomplete"
                                )
                            self._emit_status(
                                job,
                                "completed"
                                if code == 0 and job["artifact_status"] == "complete"
                                else "failed",
                            )
                        break
                time.sleep(0.05)
        except Exception as exc:
            # Unexpected monitor errors still terminate/reap owned children.
            stop_group(job.get("process_identity"), process, self.grace_s)
            with self.lock:
                job = self._get(jid)
                job.update(
                    error=f"Monitor interrupted: {type(exc).__name__}: {exc}",
                    ended_at=now(),
                    exit_code=process.returncode,
                    artifact_status="unverified",
                )
                self._emit_status(job, "interrupted")

    def cancel(self, jid):
        with self.lock:
            job = self._get(jid)
            if job["status"] in ("completed", "failed", "cancelled") and not members(
                job.get("process_identity")
            ):
                return self.get(jid)
            if not members(job.get("process_identity")):
                raise HTTPException(
                    409, "No provably owned live process group to cancel"
                )
            self._emit_status(job, "cancel_requested")
            signal_group(job["process_identity"])
            if (
                jid not in self.workers or not self.workers[jid].is_alive()
            ):  # recovered/dead monitor
                thread = threading.Thread(
                    target=self._cancel_orphan, args=(jid,), daemon=True
                )
                self.workers[jid] = thread
                thread.start()
            return self.get(jid)

    def _cancel_orphan(self, jid):
        deadline = time.monotonic() + self.grace_s
        while True:
            with self.lock:
                job = self._get(jid)
                if not members(job.get("process_identity")):
                    job.update(
                        ended_at=now(),
                        exit_code=None,
                        artifact_status="partial",
                        error="Recovered owned group terminated; original exit code unavailable",
                    )
                    self._emit_status(job, "cancelled")
                    return
                signal_group(
                    job["process_identity"], force=time.monotonic() >= deadline
                )
            time.sleep(0.05)

    def logs(self, jid, cursor=0, limit=65536):
        with self.lock:
            job = self._get(jid)
            if cursor < 0 or not 1 <= limit <= 65536:
                raise HTTPException(422, "Invalid log page")
            relative = str(Path(job["log_path"]).relative_to(self.root))
            try:
                with open_source(self.root, relative) as (h, sig):
                    if cursor > sig[2]:
                        raise HTTPException(409, "Log cursor exceeds current file size")
                    h.seek(cursor)
                    data = h.read(limit)
                return dict(
                    text=data.decode("utf-8", errors="replace"),
                    next_cursor=cursor + len(data),
                    size=sig[2],
                    complete=job["status"] in TERMINAL,
                    encoding="utf-8 with replacement",
                )
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
                return dict(
                    text="",
                    next_cursor=cursor,
                    size=0,
                    complete=job["status"] in TERMINAL,
                )

    def telemetry(self, jid, cursor=0, limit=100):
        with self.lock:
            job = self._get(jid)
            if job.get("plan", {}).get("execution_mode") != "observed":
                return None
            from control_center.services.telemetry import read_page

            try:
                page = read_page(self.root, job, cursor, limit)
                page.pop("positions", None)
                return page
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
                return None
            except (
                ValueError,
                TypeError,
                KeyError,
                RecursionError,
                OverflowError,
            ) as exc:
                raise HTTPException(
                    409, f"Recorded telemetry is invalid: {str(exc)[:200]}"
                ) from exc

    def observed_job_for_output(self, output):
        with self.lock:
            return self.store.observed_job_for_output(output)

    def events(self, jid, after=0):
        with self.lock:
            job = self._get(jid)
            if after < 0 or after > job.get("sequence", 0):
                raise HTTPException(409, "Invalid event cursor; resync snapshot")
            return self.store.events(jid, after)

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            for job in self.store.all():
                job = self._recover_identity(job)
                if members(job.get("process_identity")):
                    self.cancel(job["id"])
            workers = list(self.workers.values())
        for thread in workers:
            thread.join(timeout=self.grace_s + 5)
        os.close(self.lock_fd)
