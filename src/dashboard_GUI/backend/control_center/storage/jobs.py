"""Durable operational jobs and bounded reconnect events, never research data."""

import json
from pathlib import Path
import sqlite3


def owned_directory(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("Application-owned path contains a symlink")
    path.mkdir(parents=True, exist_ok=True)
    return path


class JobStore:
    def __init__(self, root):
        self.root = owned_directory(root)
        self.path = self.root / "jobs.sqlite"
        if self.path.is_symlink():
            raise ValueError("Unsafe jobs database")
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, record TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS events (job_id TEXT, seq INTEGER, event TEXT, PRIMARY KEY(job_id,seq))"
            )

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def save(self, job):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO jobs VALUES (?,?)", (job["id"], json.dumps(job))
            )

    def get(self, jid):
        with self.connect() as db:
            row = db.execute("SELECT record FROM jobs WHERE id=?", (jid,)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self):
        with self.connect() as db:
            return [
                json.loads(r[0])
                for r in db.execute("SELECT record FROM jobs ORDER BY rowid DESC")
            ]

    def observed_job_for_output(self, output):
        wanted = Path(output).absolute()
        with self.connect() as db:
            for row in db.execute("SELECT record FROM jobs ORDER BY rowid DESC"):
                job = json.loads(row[0])
                if (
                    job.get("plan", {}).get("execution_mode") == "observed"
                    and isinstance(job.get("output_dir"), str)
                    and Path(job["output_dir"]).absolute() == wanted
                ):
                    return job["id"]
        return None

    def event(self, job, kind, payload, now, **observation):
        job["sequence"] = job.get("sequence", 0) + 1
        event = dict(
            schema_version=1,
            job_id=job["id"],
            sequence=job["sequence"],
            type=kind,
            source="job_manager",
            sim_time_exact=None,
            sim_time_s=None,
            observed_at_utc=now,
            payload=payload,
        )
        event.update(observation)
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO jobs VALUES (?,?)", (job["id"], json.dumps(job))
            )
            db.execute(
                "INSERT INTO events VALUES (?,?,?)",
                (job["id"], job["sequence"], json.dumps(event)),
            )
            db.execute(
                "DELETE FROM events WHERE job_id=? AND seq<=?",
                (job["id"], job["sequence"] - 500),
            )

    def events(self, jid, after):
        with self.connect() as db:
            first = db.execute(
                "SELECT MIN(seq) FROM events WHERE job_id=?", (jid,)
            ).fetchone()[0]
            rows = db.execute(
                "SELECT event FROM events WHERE job_id=? AND seq>? ORDER BY seq LIMIT 100",
                (jid, after),
            ).fetchall()
        return dict(
            events=[json.loads(r[0]) for r in rows],
            resync_required=first is not None and after < first - 1,
            oldest_sequence=first,
        )
