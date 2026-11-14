"""Descriptor-relative reads: no symlink in any component, including roots.

Linux O_NOFOLLOW + dir_fd prevents check/open symlink races. Files are opened
once and rechecked before returning data. Source files are never opened writable.
"""

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import stat

from fastapi import HTTPException

ARTIFACTS = {
    "run.json",
    "metrics.json",
    "decisions.parquet",
    "outcomes.parquet",
    "features.parquet",
    "teacher_labels.parquet",
    "rule_trace.parquet",
    "decisions.csv",
    "outcomes.csv",
    "run.log",
    "report.txt",
    "bk.pl",
    "exs.pl",
}
MAX_JSON = 4 * 1024 * 1024


def signature(st):
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]


@contextmanager
def open_source(root: Path, relative: str):
    parts = Path(relative).parts
    if (
        not parts
        or Path(relative).is_absolute()
        or any(p in (".", "..") for p in parts)
    ):
        raise HTTPException(404, "Unsafe artifact path")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    handle = None
    try:
        for part in (*root.absolute().parts[1:], *parts[:-1]):
            next_fd = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = next_fd
        file_fd = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
        )
        handle = os.fdopen(file_fd, "rb")
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise HTTPException(404, "Artifact is not a regular file")
        yield handle, signature(before)
        after = os.fstat(handle.fileno())
        current = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
        if signature(before) != signature(after) or signature(before) != signature(
            current
        ):
            raise HTTPException(409, "Artifact changed during read; retry")
    except OSError as exc:
        raise HTTPException(404, "Artifact missing, inaccessible or unsafe") from exc
    finally:
        if handle is not None:
            handle.close()
        os.close(fd)


def read_json(handle):
    raw = handle.read(MAX_JSON + 1)
    if len(raw) > MAX_JSON:
        raise ValueError("JSON exceeds 4 MiB reader limit")

    def reject(value):
        raise ValueError("Nonfinite JSON number: " + value)

    def parse_float(text):
        number = float(text)
        if not math.isfinite(number):
            raise ValueError("Nonfinite JSON number")
        return number

    value = json.loads(raw, parse_constant=reject, parse_float=parse_float)
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object")
    return value


def digest(handle):
    result = hashlib.sha256()
    for block in iter(lambda: handle.read(1024 * 1024), b""):
        result.update(block)
    handle.seek(0)
    return result.hexdigest()
