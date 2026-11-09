"""Steady Gazebo playback while the simulation computes ahead (display only).

The fleet node used to draw and decide on one thread: while an allocation decision
was computed (about 0.1 s, up to several seconds for the first decision of a large
fleet) no frame was drawn and every robot froze. The Player draws frames on its own
thread at the playback pace, from a buffer the simulation fills ahead of time, so a
decision is computed while earlier frames are still being shown.

Frames are computed by the simulation thread at the moment they describe, so the
Player only sends finished poses; terminal lines go through the same buffer so the
log stays in step with what Gazebo shows. Nothing here reads or changes simulation
state: results are identical with or without it.
"""

from __future__ import annotations

import queue
import threading
from time import perf_counter, sleep
from typing import Callable

# A late frame further behind than this restarts the pace instead of rushing to catch up.
RESYNC_S = 0.25


class Player:
    """Plays queued frames at a steady wall pace on a background thread."""

    def __init__(self, send: Callable[[object], None], say: Callable[[str], None] = print,
                 buffer_s: float = 30.0, hz: float = 20.0, preroll_s: float = 6.0) -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=max(16, int(buffer_s * hz)))
        self._send = send
        self._say = say
        self.frames = 0
        self.stalls = 0  # times the buffer ran dry while frames were still coming
        # Playback starts once preroll_s of motion is buffered (or the run ends), so the
        # first decision of a large fleet, the slowest, is computed before anything moves.
        self._preroll_s = min(preroll_s, buffer_s)
        self._queued_s = 0.0
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="gazebo-playback", daemon=True)
        self._thread.start()

    def frame(self, wall_s: float, payload) -> None:
        """Queue one frame to be shown for wall_s seconds; blocks while the buffer is full."""
        self._queue.put(("frame", wall_s, payload))
        self._queued_s += wall_s
        if self._queued_s >= self._preroll_s:
            self._ready.set()

    def say(self, text: str) -> None:
        """Queue a terminal line, printed when playback reaches it."""
        self._queue.put(("say", text))

    def finish(self) -> None:
        """Play everything still queued, then stop the thread."""
        self._queue.put(("end",))
        self._ready.set()
        self._thread.join()

    def _run(self) -> None:
        due = None
        while True:
            item = self._queue.get()
            if item[0] == "end":
                return
            if item[0] == "say":
                self._say(item[1])
                continue
            _, wall_s, payload = item
            self._ready.wait()
            now = perf_counter()
            if due is None:
                due = now
            elif due < now - RESYNC_S:
                self.stalls += 1
                due = now
            due += wall_s
            delay = due - perf_counter()
            if delay > 0.0:
                sleep(delay)
            self._send(payload)
            self.frames += 1
