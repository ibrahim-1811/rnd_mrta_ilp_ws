"""Fail-closed exec gate. No simulation setup: exec the original argv unchanged.

The parent persists Linux process ownership before authorizing exec. If it dies
in the spawn window, this helper publishes identity for recovery, waits at most
10 seconds, then exits without starting scientific execution.
"""

import json
import os
from pathlib import Path
import sys
import time


def main():
    folder, nonce, *argv = sys.argv[1:]
    folder = Path(folder)
    pid = os.getpid()
    stat = Path(f"/proc/{pid}/stat").read_text()
    fields = stat[stat.rindex(")") + 2 :].split()
    record = dict(
        nonce=nonce,
        pid=pid,
        pgrp=int(fields[2]),
        session=int(fields[3]),
        start=int(fields[19]),
        boot=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
    )
    with (folder / "process_identity.json").open("x") as handle:
        json.dump(record, handle)
        handle.flush()
        os.fsync(handle.fileno())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            fd = os.open(folder / "exec_authorized", os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "r") as h:
                allowed = h.read(100) == nonce
            if allowed:
                os.execve(argv[0], argv, os.environ)
        except FileNotFoundError:
            pass
        time.sleep(0.02)
    print("Launch gate expired: no durable execution authorization", flush=True)
    return 125


if __name__ == "__main__":
    raise SystemExit(main())
