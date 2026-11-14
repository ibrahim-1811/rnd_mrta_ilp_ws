"""Linux process identity and owned-session signals; never signal a PID alone."""

import os
from pathlib import Path
import signal

BOOT = Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def identity(pid):
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
        fields = text[text.rindex(")") + 2 :].split()
        return dict(
            pid=pid,
            state=fields[0],
            pgrp=int(fields[2]),
            session=int(fields[3]),
            start=int(fields[19]),
            boot=BOOT,
        )
    except (OSError, ValueError, IndexError):
        return None


def members(owned):
    if not owned or owned.get("boot") != BOOT:
        return []
    pid = owned["pid"]
    if owned.get("pgrp") != pid or owned.get("session") != pid:
        return []
    leader = identity(pid)
    if leader and any(
        leader[k] != owned.get(k) for k in ("start", "pgrp", "session", "boot")
    ):
        return []
    result = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        item = identity(int(entry.name))
        if (
            item
            and item["pgrp"] == pid
            and item["session"] == pid
            and item["start"] >= owned["start"]
            and item["state"] != "Z"
        ):
            result.append(item)
    return result


def signal_group(owned, *, force=False):
    if not members(owned):
        return False
    try:
        os.killpg(owned["pid"], signal.SIGKILL if force else signal.SIGTERM)
        return True
    except ProcessLookupError:
        return False


def stop_group(owned, process=None, grace_s=2.0):
    """Escalate based on the whole group's liveness, not just its leader."""
    import time

    signal_group(owned)
    deadline = time.monotonic() + grace_s
    while members(owned) and time.monotonic() < deadline:
        time.sleep(0.02)
    signal_group(owned, force=True)
    if process is not None:
        process.wait(timeout=3)
    deadline = time.monotonic() + 3
    while members(owned) and time.monotonic() < deadline:
        time.sleep(0.02)
    return not members(owned)
