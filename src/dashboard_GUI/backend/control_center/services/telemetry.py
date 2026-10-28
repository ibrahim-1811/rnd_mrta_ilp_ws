"""Bounded, validated reads of app-owned sampled traces. Never advance science."""
from datetime import datetime, timezone
from copy import deepcopy
from fractions import Fraction
import json
import math
import re

from fastapi import HTTPException
from control_center.services.observation import MAX_RECORD_BYTES
from control_center.storage.files import open_source

MAX_PAGE_BYTES = 1024 * 1024
TYPES = {'telemetry_header', 'fleet_snapshot', 'telemetry_end'}


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def count(value):
    return type(value) is int and value >= 0


def exact(value):
    if not isinstance(value, str) or len(value) > 128 or not re.fullmatch(r'-?[0-9]+(?:/[1-9][0-9]*)?', value):
        raise ValueError('Invalid exact time/SOC')
    return Fraction(value)


def cell(value):
    return isinstance(value, list) and len(value) == 2 and all(type(n) is int for n in value)


def validate(record, jid, fingerprint):
    if (not isinstance(record, dict) or type(record.get('schema_version')) is not int
            or record['schema_version'] != 1 or record.get('job_id') != jid
            or record.get('type') not in TYPES or record.get('source') != 'executor_observer'
            or not isinstance(record.get('stream_generation'), str)
            or not re.fullmatch('[a-f0-9]{32}', record['stream_generation'])
            or not isinstance(record.get('payload'), dict)):
        raise ValueError('Unsupported telemetry schema, source or identity')
    payload = record['payload']
    if record['type'] == 'telemetry_header':
        if (payload.get('source_fingerprint') != fingerprint or payload.get('adapter_version') != 1
                or payload.get('exact_time') is not True or exact(payload.get('sample_interval_exact')) <= 0):
            raise ValueError('Telemetry source fingerprint/adapter does not match this job')
    elif record['type'] == 'telemetry_end':
        if (any(type(payload.get(k)) is not bool for k in ('complete', 'scientific_run_returned', 'truncated'))
                or not count(payload.get('dropped_samples_total')) or not count(payload.get('writer_dropped_samples'))
                or (payload.get('error') is not None and not isinstance(payload['error'], str))):
            raise ValueError('Invalid trace end marker')
        if payload['complete'] and (not payload['scientific_run_returned'] or payload['truncated'] or payload.get('error')):
            raise ValueError('Inconsistent trace completion marker')
    else:
        if (not count(record.get('sample_sequence')) or record['sample_sequence'] == 0
                or not number(record.get('sim_time_s')) or float(exact(record.get('sim_time_exact'))) != record['sim_time_s']
                or not isinstance(record.get('observed_at_utc'), str) or len(record['observed_at_utc']) > 100
                or payload.get('sample_mode') != 'sampled' or not count(payload.get('dropped_samples_total'))):
            raise ValueError('Invalid snapshot time, sequence or sampling metadata')
        stamp = datetime.fromisoformat(record['observed_at_utc'])
        if stamp.tzinfo is None or stamp.utcoffset() != timezone.utc.utcoffset(stamp):
            raise ValueError('Snapshot observation timestamp must be UTC')
        orders = payload.get('orders')
        if not isinstance(orders, dict) or any(not count(orders.get(k)) for k in
                ('total', 'pending', 'assigned', 'in_progress', 'completed', 'failed', 'deferred_now', 'cumulative_deferrals')):
            raise ValueError('Invalid observed order counts')
        robots = payload.get('robots')
        resources = payload.get('resources')
        if not isinstance(robots, list) or len(robots) > 1000 or not isinstance(resources, list) or len(resources) > 10000:
            raise ValueError('Snapshot collection exceeds reader bounds')
        from simulation_env.grid_robot import GridRobotStatus
        statuses = {s.value for s in GridRobotStatus}
        identifiers = set()
        for robot in robots:
            if (not isinstance(robot, dict) or not isinstance(robot.get('robot_id'), str)
                    or not 1 <= len(robot['robot_id']) <= 256 or robot['robot_id'] in identifiers
                    or not cell(robot.get('cell')) or robot.get('status') not in statuses):
                raise ValueError('Invalid observed robot identity/cell/status')
            identifiers.add(robot['robot_id'])
            for key in ('distance_m', 'travel_time_s', 'waiting_time_s', 'service_time_s', 'charging_time_s'):
                if not number(robot.get(key)) or robot[key] < 0:
                    raise ValueError('Invalid robot counter')
            for key in ('wait_event_count', 'vertex_conflict_count', 'edge_conflict_count'):
                if not count(robot.get(key)):
                    raise ValueError('Invalid observed conflict count')
            if robot.get('soc_exact') is None:
                if robot.get('soc_percent') is not None:
                    raise ValueError('Unavailable battery has a fabricated SOC')
            elif not number(robot.get('soc_percent')) or float(exact(robot['soc_exact'])) != robot['soc_percent']:
                raise ValueError('SOC differs from exact observed value')
        for slot in resources:
            if not isinstance(slot, dict) or not cell(slot.get('cell')) or slot.get('state') not in ('FREE', 'RESERVED', 'OCCUPIED', 'VACATING'):
                raise ValueError('Invalid observed resource state')
    return record


def read_page(root, job, cursor=0, limit=100):
    if cursor < 0 or not 1 <= limit <= 100:
        raise HTTPException(422, 'Invalid telemetry page')
    with open_source(root, f"jobs/{job['id']}/telemetry.ndjson") as (handle, sig):
        if cursor > sig[2]:
            raise HTTPException(409, 'Trace cursor exceeds current file size')
        handle.seek(cursor)
        raw = handle.read(MAX_PAGE_BYTES)
    records, positions = [], []
    next_cursor = cursor
    partial = False
    def nonfinite(text):
        raise ValueError('Nonfinite telemetry number')
    for line in raw.splitlines(keepends=True):
        if len(records) >= limit:
            break
        if len(line) > MAX_RECORD_BYTES:
            raise ValueError('Telemetry record exceeds 256 KiB reader bound')
        if not line.endswith(b'\n'):
            partial = True
            break
        record = json.loads(line, parse_constant=nonfinite)
        records.append(validate(record, job['id'], job['plan']['inputs_hash']))
        next_cursor += len(line)
        positions.append(next_cursor)
    return dict(records=records, positions=positions, next_cursor=next_cursor,
                has_more=next_cursor < sig[2], trailing_partial=partial, file_identity=sig[:2], size=sig[2])


def ingest(manager, job):
    """Called under manager.lock. Source cursor and each event commit together."""
    if job.get('plan', {}).get('execution_mode') != 'observed':
        return False
    before = deepcopy(job)
    metadata = job.setdefault('telemetry', dict(available=False, availability='pending', recording_complete=False))
    try:
        page = read_page(manager.root, job, job.get('telemetry_cursor', 0))
        if metadata.get('footer') is not None and page['has_more']:
            raise ValueError('Data appeared after the trace end marker')
        if job.get('telemetry_file_identity') not in (None, page['file_identity']):
            raise ValueError('Trace file was replaced; current state is unverified')
        job['telemetry_file_identity'] = page['file_identity']
        progressed = False
        for record, position in zip(page['records'], page['positions']):
            kind = record['type']
            generation = record['stream_generation']
            if metadata.get('footer') is not None:
                raise ValueError('Record appeared after the trace end marker')
            if kind == 'telemetry_header':
                if job.get('telemetry_cursor', 0) != 0:
                    raise ValueError('Unexpected stream header inside trace')
                metadata.update(stream_generation=generation, source='executor_observer')
            elif metadata.get('stream_generation') != generation:
                raise ValueError('Trace stream generation changed')
            if kind == 'fleet_snapshot':
                previous = metadata.get('sample_sequence', 0)
                if record['sample_sequence'] <= previous:
                    raise ValueError('Sample sequence moved backwards')
                if record['sample_sequence'] > previous + 1:
                    metadata['gap'] = True
                    manager.store.event(job, 'telemetry_gap', dict(reason='Observer samples were dropped',
                        after_sample=previous, next_sample=record['sample_sequence']), manager_time())
                job['telemetry_cursor'] = position
                metadata.update(available=True, availability='available', sample_sequence=record['sample_sequence'],
                                dropped_samples_total=record['payload']['dropped_samples_total'])
                metadata.pop('reason', None)
                job['snapshot'] = dict(record, sequence=job.get('sequence', 0) + 1)
                manager.store.event(job, 'fleet_snapshot', record['payload'], record['observed_at_utc'],
                    source='executor_observer', sim_time_exact=record['sim_time_exact'], sim_time_s=record['sim_time_s'],
                    stream_generation=generation, sample_sequence=record['sample_sequence'])
            elif kind == 'telemetry_end':
                footer = record['payload']
                complete = (metadata.get('available') and footer['complete'] and not metadata.get('gap')
                            and not footer['dropped_samples_total'] and not footer['writer_dropped_samples'])
                job['telemetry_cursor'] = position
                metadata.update(recording_complete=bool(complete), availability='available' if complete else 'partial', footer=footer)
                manager.store.event(job, 'artifact_available', dict(artifact='telemetry', **metadata), manager_time())
            else:
                job['telemetry_cursor'] = position
                manager.store.save(job)
            progressed = True
        if metadata.get('footer') is not None and page['has_more']:
            raise ValueError('Partial data appeared after the trace end marker')
        if metadata.get('available') and not metadata.get('recording_complete') and job['status'] in ('completed', 'failed', 'cancelled', 'interrupted'):
            metadata['availability'] = 'partial'
        manager.store.save(job)
        return progressed and page['has_more']
    except HTTPException as exc:
        if exc.status_code == 409:
            if exc.detail == 'Trace cursor exceeds current file size':
                return invalidate(manager, job, 'Trace was truncated after ingestion; previous state is unverified')
            return False  # Growing source, retry; no guessed or torn state.
        if exc.status_code == 404:
            metadata.update(available=False, availability='unavailable' if job['status'] in ('completed', 'failed', 'cancelled', 'interrupted') else 'pending',
                            reason='No recorded fleet observation is available yet')
            manager.store.save(job)
            return False
        raise
    except Exception as exc:
        # Observation is optional. Reader/import/codec errors must not enter the
        # process monitor's scientific failure/cancellation path.
        restore(manager, job, before)
        return invalidate(manager, job, f'{type(exc).__name__}: {exc}'[:300])


def restore(manager, job, fallback):
    try:
        committed = manager.store.get(job['id']) or fallback
    except Exception:
        committed = fallback
    job.clear()
    job.update(committed)


def invalidate(manager, job, reason):
    before = deepcopy(job)
    metadata = job.setdefault('telemetry', {})
    changed = metadata.get('reason') != reason or metadata.get('availability') != 'invalid'
    metadata.update(available=False, availability='invalid', recording_complete=False, reason=reason)
    try:
        if changed:
            manager.store.event(job, 'telemetry_gap', dict(reason=reason, available=False), manager_time())
        else:
            manager.store.save(job)
    except Exception:
        # Error reporting is optional too. Keep the last committed source cursor
        # so a transient DB failure cannot skip an uncommitted sample.
        restore(manager, job, before)
        job.setdefault('telemetry', {}).update(available=False, availability='invalid',
                                              recording_complete=False, reason=reason)
    return False


def manager_time():
    from control_center.services.observation import utc_now
    return utc_now()
