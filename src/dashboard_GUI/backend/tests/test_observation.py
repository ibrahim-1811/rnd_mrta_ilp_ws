"""Tiny parity and failure checks for observational copying and recording."""
from dataclasses import replace
from fractions import Fraction
import json
from queue import Queue
import threading

import pytest


def executor_fixture(*, battery=True, charging=False, callback=None):
    from control_center.adapters.catalogue import load_scenario
    from mrta_core.order_queue import OrderQueue
    from mrta_core.orders import load_orders_csv, select_orders
    from mrta_core.warehouse_map import ZoneType
    from simulation_env.dataset import RunRecorder
    from simulation_env.fleet import build_fleet
    from simulation_env.workload_executor import WorkloadExecutor
    from task_management.allocation_strategy import get_batch_strategy
    config, warehouse = load_scenario()
    config = replace(config, robot_count=2, battery=replace(config.battery, enabled=battery,
                      initial_soc_percent=Fraction(10 if charging else 100)))
    recorder = RunRecorder(run_id='parity')
    executor = WorkloadExecutor(warehouse, build_fleet(config, warehouse),
        OrderQueue(select_orders(load_orders_csv(config.orders_path, warehouse), 3)),
        get_batch_strategy('random', seed=7), resolution=config.resolution_m, speed_mps=config.speed_mps,
        service_seconds={ZoneType.SHELF: config.shelf_pick_s, ZoneType.DROPOFF: config.dropoff_s},
        progress_interval_s=0, battery=config.battery, window=config.allocation_window,
        dispatch_quantum_s=3, recorder=recorder, strategy_name='random', on_tick=callback)
    return executor, recorder


def test_snapshot_is_a_primitive_copy_of_authoritative_state():
    from control_center.services.observation import SnapshotObserver
    executor, _ = executor_fixture()
    executor.engine.time = Fraction(0)
    queue = Queue(2)
    observer = SnapshotObserver(executor, queue, interval_s=1)
    observer.capture(force=True)
    snapshot = queue.get_nowait()
    first = snapshot['payload']['robots'][0]
    robot = executor.engine.robots[first['robot_id']]
    assert first['cell'] == list(robot.cell)
    assert first['soc_exact'] == '100'
    assert snapshot['sim_time_exact'] == '0' and snapshot['sim_time_s'] == 0
    assert snapshot['payload']['orders']['total'] == 3
    original = json.dumps(snapshot, sort_keys=True)
    robot.cell = (999, 999)
    assert json.dumps(snapshot, sort_keys=True) == original


def test_disabled_battery_is_null_and_repeated_times_have_distinct_samples():
    from control_center.services.observation import SnapshotObserver
    executor, _ = executor_fixture(battery=False)
    queue = Queue(2)
    observer = SnapshotObserver(executor, queue)
    observer.capture(force=True)
    observer.capture(force=True)
    a, b = queue.get_nowait(), queue.get_nowait()
    assert a['sim_time_exact'] == b['sim_time_exact'] == '0'
    assert a['sample_sequence'] < b['sample_sequence']
    assert a['payload']['robots'][0]['soc_exact'] is None
    assert a['payload']['robots'][0]['soc_percent'] is None


@pytest.mark.parametrize('mode', ['normal', 'overflow', 'copy_error', 'charging'])
def test_observer_preserves_events_decisions_outcomes_and_final_state(mode, monkeypatch):
    from control_center.services.observation import SnapshotObserver
    def run(observe):
        events = []
        observer = None
        def tick(_, batch):
            events.extend((str(e.time), e.seq, e.robot_id, e.kind.value, e.detail) for e in batch)
            if observer is not None:
                observer.on_tick(_, batch)
        executor, recorder = executor_fixture(charging=mode == 'charging', callback=tick)
        queue = Queue(1 if mode == 'overflow' else 10000)
        if observe:
            observer = SnapshotObserver(executor, queue, interval_s=.001)
            if mode == 'copy_error':
                def broken():
                    raise RuntimeError('copy failed')
                monkeypatch.setattr(observer, 'snapshot', broken)
            observer.capture(force=True)
        result = executor.run()
        final = [(rid, r.cell, r.distance_m, r.waiting_time_s,
                  None if executor.engine.battery is None else str(executor.engine.battery.soc(rid, executor.engine.time)))
                 for rid, r in sorted(executor.engine.robots.items())]
        return (events, recorder.decisions, recorder.outcomes, result.decision_log, final,
                str(executor.engine.time), [(d.robot_id, d.order_id, d.sim_time) for d in result.dispatches]), observer, queue
    baseline, _, _ = run(False)
    observed, observer, queue = run(True)
    assert observed == baseline
    if mode == 'overflow':
        assert observer.dropped_samples > 0 and queue.qsize() == 1
    if mode == 'copy_error':
        assert observer.error == 'RuntimeError: copy failed'
    if mode == 'charging':
        samples = [queue.get_nowait() for _ in range(queue.qsize())]
        assert any(r['charge_state'] == 'CHARGING' for s in samples for r in s['payload']['robots'])


def test_writer_records_header_samples_footer_and_owns_a_bounded_queue(tmp_path):
    from control_center.services.observation import TraceRecorder
    executor, _ = executor_fixture()
    recorder = TraceRecorder(tmp_path / 'telemetry.ndjson', 'job', 'source', executor=executor)
    recorder.observer.capture(force=True)
    executor.engine.time = Fraction(2, 3)
    recorder.observer.capture(force=True)
    recorder.close(complete=True)
    records = [json.loads(line) for line in (tmp_path / 'telemetry.ndjson').read_text().splitlines()]
    assert records[0]['type'] == 'telemetry_header'
    assert records[1]['sim_time_exact'] == '0'
    assert records[2]['sim_time_exact'] == '2/3'
    assert records[-1]['type'] == 'telemetry_end' and records[-1]['payload']['complete'] is True
    assert recorder.queue.maxsize == 128
    assert all(r['job_id'] == 'job' and r['schema_version'] == 1 for r in records)


def test_writer_failure_cannot_enter_simulator_callback(tmp_path):
    from control_center.services.observation import TraceRecorder
    parent = tmp_path / 'file'
    parent.write_text('not a directory')
    executor, _ = executor_fixture(callback=lambda t, batch: recorder.observer.on_tick(t, batch))
    recorder = TraceRecorder(parent / 'telemetry.ndjson', 'job', 'source', executor=executor)
    executor.run()
    recorder.close(complete=True)
    assert recorder.error
    assert executor.snapshot().completed == 3


def test_trace_quota_preserves_scientific_success_and_marks_truncation(tmp_path):
    from control_center.services.observation import TraceRecorder
    executor, _ = executor_fixture(callback=lambda t, batch: recorder.observer.on_tick(t, batch))
    path = tmp_path / 'telemetry.ndjson'
    recorder = TraceRecorder(path, 'job', 'source', executor=executor, max_bytes=4096)
    executor.run()
    recorder.close(complete=True)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert path.stat().st_size <= 4096
    assert records[-1]['payload']['truncated'] is True
    assert executor.snapshot().completed == 3


def test_final_state_is_retained_when_sampling_queue_is_full(tmp_path, monkeypatch):
    from control_center.services.observation import TraceRecorder
    original = TraceRecorder._write
    delay = threading.Event()
    def delayed(self):
        delay.wait(timeout=.5)
        original(self)
    monkeypatch.setattr(TraceRecorder, '_write', delayed)
    executor, _ = executor_fixture()
    path = tmp_path / 'telemetry.ndjson'
    recorder = TraceRecorder(path, 'job', 'source', executor=executor)
    for _ in range(140):
        recorder.observer.capture(force=True)
    assert recorder.observer.dropped_samples > 0
    executor.engine.time = Fraction(5, 3)
    recorder.close(complete=True)
    samples = [json.loads(line) for line in path.read_text().splitlines() if json.loads(line)['type'] == 'fleet_snapshot']
    assert samples[-1]['sim_time_exact'] == '5/3'
