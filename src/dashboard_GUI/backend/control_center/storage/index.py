"""Disposable operational SQLite cache. Scientific values are read from artifacts."""
import csv
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import threading

from fastapi import HTTPException
from control_center.adapters.tables import rows
from control_center.services.pagination import fingerprint
from control_center.storage.files import ARTIFACTS, digest, open_source, read_json


class ArtifactReader:
    """Read and classify a directory without creating a cache or writing files."""

    def __init__(self, root):
        self.root = Path(root).absolute()

    def inspect(self, directory):
        rid = fingerprint([str(self.root), directory])[:32]
        artifacts = [self.inspect_artifact(directory, name, None) for name in sorted(ARTIFACTS)]
        return classify(rid, directory, artifacts)

    def inspect_artifact(self, directory, name, cached):
        item = dict(id=name, availability='missing', size=None, sha256=None)
        try:
            with open_source(self.root, str(Path(directory) / name)) as (handle, sig):
                if cached and cached.get('signature') == sig and cached.get('validator_version') == 1:
                    return cached
                item.update(validator_version=1, signature=sig, size=sig[2], sha256=digest(handle), availability='valid')
                if name.endswith('.json'):
                    metadata = read_json(handle)
                    if name == 'metrics.json':
                        item['metrics_schema'] = metric_availability(metadata)
                        metadata = {k: metadata.get(k) for k in ('metrics_schema_version', 'orders', 'orders_completed', 'strategy')}
                    item['metadata'] = metadata
                elif name.startswith(('decisions.', 'outcomes.')):
                    count = 0
                    run_ids = set()
                    for row in rows(handle, name):
                        if not isinstance(row.get('run_id'), str) or not row['run_id']:
                            raise ValueError('Missing row run_id')
                        run_ids.add(row['run_id'])
                        if len(run_ids) > 1:
                            raise ValueError('Mixed run identities in table')
                        count += 1
                    item['run_ids'] = sorted(run_ids)
                    item['rows'] = count
                elif name == 'run.log':
                    # Bounded evidence, not a claim that arbitrary old logs have a status protocol.
                    head = handle.read(65536)
                    handle.seek(max(0, sig[2] - 65536))
                    tail = handle.read(65536)
                    lines = (head + b'\n' + tail).decode('utf-8', errors='replace').splitlines()
                    if any(line.startswith('STALLED') for line in lines):
                        item['process_evidence'] = 'stalled'
                    elif any(line == 'Traceback (most recent call last):' for line in lines):
                        item['process_evidence'] = 'failed'
        except HTTPException as exc:
            item.update(availability='unavailable', warning=exc.detail)
        except (ValueError, TypeError, UnicodeError, OSError, csv.Error) as exc:
            item.update(availability='invalid', warning=str(exc)[:200])
        return item


class RunIndex(ArtifactReader):
    def __init__(self, root, data_dir):
        super().__init__(root)
        data_dir = Path(data_dir).resolve()
        if data_dir == self.root.resolve() or self.root.resolve() in data_dir.parents:
            raise ValueError('Application data cannot be inside scientific history')
        data_dir.mkdir(parents=True, exist_ok=True)
        self.database = data_dir / 'index.sqlite'
        self.lock = threading.RLock()
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, directory TEXT, record TEXT, indexed_at TEXT)')

    def connect(self):
        return sqlite3.connect(self.database, timeout=30)

    def rebuild(self):
        with self.lock, self.connect() as db:
            previous = {row[0]: json.loads(row[1]) for row in db.execute('SELECT id,record FROM runs')}
            records = []
            # os.walk does not follow symlink directories. Every subsequent file open
            # also traverses components with O_NOFOLLOW, protecting against swaps.
            for path, directories, names in os.walk(self.root, followlinks=False):
                directories[:] = sorted(d for d in directories if not (Path(path) / d).is_symlink())
                if not set(names) & ARTIFACTS:
                    continue
                directory = str(Path(path).relative_to(self.root))
                rid = fingerprint([str(self.root), directory])[:32]
                old = {a['id']: a for a in previous.get(rid, {}).get('artifacts', [])}
                artifacts = [self.inspect_artifact(directory, name, old.get(name)) for name in sorted(ARTIFACTS)]
                record = classify(rid, directory, artifacts)
                records.append(record)
            db.execute('DELETE FROM runs')
            now = datetime.now(timezone.utc).isoformat()
            db.executemany('INSERT INTO runs VALUES (?,?,?,?)',
                           [(r['id'], r['directory'], json.dumps(r), now) for r in records])
        return sorted(records, key=lambda r: r['id'])

    def get(self, rid):
        records = self.rebuild()
        for record in records:
            if record['id'] == rid:
                return record
        raise HTTPException(404, 'Unknown run ID')


def classify(rid, directory, artifacts):
    by_name = {a['id']: a for a in artifacts}
    manifest = by_name['run.json'].get('metadata')
    metrics = by_name['metrics.json'].get('metadata')
    warnings = []
    valid_manifest = isinstance(manifest, dict) and all(k in manifest for k in
        ('run_id', 'schema_version', 'format', 'decision_rows', 'outcome_rows'))
    valid_manifest = valid_manifest and type(manifest['schema_version']) is int and manifest['schema_version'] == 1
    valid_manifest = valid_manifest and isinstance(manifest['run_id'], str) and bool(manifest['run_id'])
    valid_manifest = valid_manifest and all(type(manifest[k]) is int and manifest[k] >= 0
                                           for k in ('decision_rows', 'outcome_rows'))
    if not valid_manifest:
        warnings.append('Missing or invalid manifest; insufficient provenance')
    fmt = manifest.get('format') if isinstance(manifest, dict) else None
    required = ['run.json', 'metrics.json']
    if fmt in ('csv', 'parquet'):
        required += [f'decisions.{fmt}', f'outcomes.{fmt}']
    else:
        warnings.append('Unknown dataset format')
    complete = valid_manifest and fmt in ('csv', 'parquet') and all(by_name[n]['availability'] == 'valid' for n in required)
    if complete:
        for kind, key in [('decisions', 'decision_rows'), ('outcomes', 'outcome_rows')]:
            table = by_name[f'{kind}.{fmt}']
            if table.get('rows') != manifest[key] or any(x != manifest['run_id'] for x in table.get('run_ids', [])):
                complete = False
                warnings.append(f'{kind} row count does not match manifest')
    metric_state = by_name['metrics.json'].get('metrics_schema', 'missing' if metrics is None else 'invalid')
    if metric_state not in ('legacy', 'current'):
        complete = False
    if metric_state == 'legacy':
        warnings.append('Legacy metrics retained unchanged; fleet makespan is not order makespan')
    if valid_manifest and (type(manifest.get('orders')) is not int or manifest['orders'] < 0):
        complete = False
        warnings.append('Missing or invalid expected order count in manifest; completion cannot be validated')
    if complete and (metrics.get('orders') != manifest['orders'] or metrics.get('orders_completed') != manifest['orders'] or manifest['outcome_rows'] != manifest['orders']):
        complete = False
        warnings.append('Order completion counts do not agree')
    provenance = 'present' if valid_manifest and all(manifest.get(k) for k in ('git', 'config_sha256', 'map_sha256', 'orders_sha256')) else 'insufficient'
    if provenance == 'insufficient':
        warnings.append('Insufficient source/input provenance')
    process = manifest.get('status', 'unknown') if isinstance(manifest, dict) else 'unknown'
    # Legacy run manifests have no exit evidence. Never infer process success.
    if process not in ('failed', 'cancelled', 'stalled'):
        process = by_name['run.log'].get('process_evidence', 'unknown')
    if process != 'unknown':
        warnings.append('Failure/cancellation evidence is present; inspect original manifest/log')
    warnings.append('No verified process exit evidence; artifact completeness is separate from process success')
    return dict(id=rid, directory=directory, manifest=manifest,
                scientific_status='completed' if complete and provenance == 'present' and process == 'unknown' else 'unknown',
                integrity='complete' if complete else 'incomplete', process_status=process,
                provenance=provenance, metrics_schema=metric_state,
                dataset_schema_version=manifest.get('schema_version') if isinstance(manifest, dict) else None,
                artifacts=artifacts, warnings=warnings)


def metric_availability(metrics):
    if metrics is None:
        return 'missing'
    version = metrics.get('metrics_schema_version', 1)  # Documented v1 has no version field.
    if type(version) is not int or version not in (1, 2):
        return 'unsupported'
    for name in ('orders', 'orders_completed'):
        if type(metrics.get(name)) is not int or metrics[name] < 0:
            return 'invalid'
    required = ['makespan_s']
    if version == 2:
        required += ['order_makespan_s', 'fleet_recovery_time_s', 'order_throughput_per_h']
    for key in required:
        if key not in metrics:
            return 'invalid'
        value = metrics[key]
        if value is None and key == 'order_makespan_s' and metrics['orders_completed'] == 0:
            continue
        if type(value) not in (int, float) or (isinstance(value, float) and not math.isfinite(value)) or value < 0:
            return 'invalid'
    return 'current' if version == 2 else 'legacy'
