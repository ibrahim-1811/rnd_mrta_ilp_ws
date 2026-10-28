"""Primitive post-tick copies and bounded recording; stdlib only in the child."""
from datetime import datetime, timezone
from fractions import Fraction
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
import threading
import uuid

MAX_RECORD_BYTES = 256 * 1024
MAX_TRACE_BYTES = 64 * 1024 * 1024


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class SnapshotObserver:
    def __init__(self, executor, queue, interval_s=1):
        self.executor, self.queue = executor, queue
        self.interval = Fraction(str(interval_s))
        if self.interval <= 0:
            raise ValueError('Observation interval must be positive')
        self.next_sample = Fraction(0)
        self.sample_sequence = 0
        self.dropped_samples = 0
        self.error = None

    def snapshot(self):
        executor = self.executor
        exact_time = executor.engine.time
        robots = []
        for rid, robot in sorted(executor.engine.robots.items()):
            charge = executor.charge.get(rid)
            deferred = executor.deferred.get(rid)
            soc = None if executor.engine.battery is None else executor.engine.battery.soc(rid, exact_time)
            robots.append(dict(robot_id=rid, cell=list(robot.cell), status=robot.status.value,
                active_order_id=executor.active.get(rid),
                assigned_order_id=deferred.order_id if deferred else executor.active.get(rid),
                charge_state=charge.state.value if charge else None,
                soc_percent=None if soc is None else float(soc), soc_exact=None if soc is None else str(soc),
                distance_m=robot.distance_m, travel_time_s=robot.travel_time_s,
                waiting_time_s=robot.waiting_time_s, service_time_s=robot.service_time_s,
                charging_time_s=robot.charging_time_s, wait_event_count=robot.wait_event_count,
                vertex_conflict_count=robot.vertex_conflict_count, edge_conflict_count=robot.edge_conflict_count))
        progress = executor.snapshot()
        orders = {key: getattr(progress, key) for key in
                  ('total', 'pending', 'assigned', 'in_progress', 'completed', 'failed', 'deferred_now', 'cumulative_deferrals')}
        resources = []
        for bank in (executor.chargers, executor.holding):
            if bank is not None:
                resources.extend(dict(kind=bank.kind, zone_id=slot.zone_id, cell=list(slot.cell),
                    state=slot.state.value, robot_id=slot.robot_id, promised_to=slot.promised_to)
                    for _, slot in sorted(bank.slots.items()))
        return dict(type='fleet_snapshot', source='executor_observer', sample_sequence=self.sample_sequence,
            sim_time_exact=str(exact_time), sim_time_s=float(exact_time), observed_at_utc=utc_now(),
            payload=dict(sample_mode='sampled', dropped_samples_total=self.dropped_samples,
                         robots=robots, orders=orders, resources=resources))

    def capture(self, *, force=False):
        if self.error is not None:
            return
        try:
            now = self.executor.engine.time
            if not force and now < self.next_sample:
                return
            self.next_sample = now + self.interval
            self.sample_sequence += 1
            record = self.snapshot()
            try:
                self.queue.put_nowait(record)
            except Full:
                self.dropped_samples += 1
        except Exception as exc:
            self.error = f'{type(exc).__name__}: {exc}'[:300]

    def on_tick(self, sim_time, batch):
        # Use the authoritative Fraction clock, never the float callback time.
        self.capture()


class TraceRecorder:
    def __init__(self, path, job_id, source_fingerprint, *, executor, interval_s=1,
                 max_bytes=MAX_TRACE_BYTES):
        self.path = Path(path)
        self.job_id = job_id
        self.generation = uuid.uuid4().hex
        self.source_fingerprint = source_fingerprint
        self.max_bytes = max_bytes
        self.queue = Queue(maxsize=128)
        self.observer = SnapshotObserver(executor, self.queue, interval_s)
        self.done = threading.Event()
        self.scientific_complete = False
        self.truncated = False
        self.writer_drops = 0
        self._error = None
        self.final_sample = None
        self.thread = threading.Thread(target=self._write, name='observation-writer', daemon=True)
        self.thread.start()

    @property
    def error(self):
        return self._error or self.observer.error

    def envelope(self, record):
        return dict(schema_version=1, job_id=self.job_id, stream_generation=self.generation, **record)

    def encode(self, record):
        data = (json.dumps(self.envelope(record), separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
        if len(data) > MAX_RECORD_BYTES:
            raise ValueError('Observation record exceeds 256 KiB bound')
        return data

    def _write(self):
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                header = self.encode(dict(type='telemetry_header', source='executor_observer',
                    payload=dict(source_fingerprint=self.source_fingerprint, adapter_version=1,
                                 sample_interval_exact=str(self.observer.interval), exact_time=True)))
                stream.write(header)
                stream.flush()
                size = len(header)
                while not self.done.is_set() or not self.queue.empty():
                    try:
                        sample = self.queue.get(timeout=.05)
                    except Empty:
                        continue
                    encoded = self.encode(sample)
                    if size + len(encoded) + 1024 > self.max_bytes:
                        self.truncated = True
                        self.writer_drops += 1
                        continue
                    stream.write(encoded)
                    stream.flush()
                    size += len(encoded)
                # A dedicated bounded final slot is filled only after the run,
                # so queue saturation cannot discard the final fleet state.
                if self.final_sample is not None:
                    encoded = self.encode(self.final_sample)
                    if size + len(encoded) + 1024 <= self.max_bytes:
                        stream.write(encoded)
                        stream.flush()
                        size += len(encoded)
                    else:
                        self.truncated = True
                        self.writer_drops += 1
                footer = self.encode(dict(type='telemetry_end', source='executor_observer',
                    payload=dict(complete=bool(self.scientific_complete and not self.error and not self.truncated),
                        scientific_run_returned=self.scientific_complete, truncated=self.truncated,
                        dropped_samples_total=self.observer.dropped_samples,
                        writer_dropped_samples=self.writer_drops, error=self.error)))
                if size + len(footer) <= self.max_bytes:
                    stream.write(footer)
                    stream.flush()
        except Exception as exc:
            self._error = f'{type(exc).__name__}: {exc}'[:300]
            self.observer.error = 'Writer unavailable: ' + self._error

    def close(self, *, complete=False):
        # Called outside simulator callbacks. It may wait briefly for the writer.
        if self.observer.error is None:
            try:
                self.observer.sample_sequence += 1
                self.final_sample = self.observer.snapshot()
            except Exception as exc:
                self.observer.error = f'{type(exc).__name__}: {exc}'[:300]
        self.scientific_complete = complete
        self.done.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            self._error = 'Writer did not finish within five seconds; trace is partial'
