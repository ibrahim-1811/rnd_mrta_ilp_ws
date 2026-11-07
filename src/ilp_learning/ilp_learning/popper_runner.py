"""Isolated subprocess runner for Popper v4.4.0.

Enforces:
1. Subprocess isolation: prevents internal global Clingo state leakage across invocations.
2. Determinism: runs with PYTHONHASHSEED=0 and unsets conflicting PYTHONPATH.
3. Structured output: parses discovered rules, score tuple (TP, FN, TN, FP, size), and timing.
4. Anytime result: the worker reports every new best program as Popper finds it, so a
   worker that overruns its budget plus WORKER_GRACE_S (one long solver call can delay
   Popper's own timeout) and is stopped still yields the best program it had found,
   marked stopped_by_runner, instead of no result at all.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import time


# Solver budget, M2 spec section 5.5. Popper's own defaults (1200 s, 0.001 s,
# 40 literals) are not the settings the spec sized the search for.
DEFAULT_TIMEOUT_S = 300
DEFAULT_EVAL_TIMEOUT_S = 0.05
DEFAULT_MAX_LITERALS = 24
# Popper checks its timeout between search steps; this bounds a stuck worker.
WORKER_GRACE_S = 60


@dataclass(frozen=True)
class PopperResult:
    success: bool
    rules: list[str]
    score: tuple[int, int, int, int, int] | None  # (tp, fn, tn, fp, size)
    datalog: bool
    total_time_s: float
    raw_output: str
    timed_out: bool = False
    # The worker overran timeout + WORKER_GRACE_S and was stopped; rules and score are
    # the last best program it reported (Popper's incumbent), not its final answer.
    stopped_by_runner: bool = False


_WORKER_SCRIPT = """
import json
import sys
from popper.loop import learn_solution
from popper.util import Settings, format_prog

# Popper calls this at every new best program (its incumbent). Report each one, flushed,
# so the runner keeps the best program even if it must stop an overrunning worker.
_incumbent = Settings.print_incomplete_solution2

def _report_incumbent(self, prog, tp, fn, tn, fp, size):
    rules = [format_prog([(head, body)]).strip() for head, body in prog]
    score = [int(tp), int(fn), int(tn), int(fp), int(size)]
    print("POPPER_INCUMBENT_JSON:" + json.dumps(
        {"rules": rules, "score": score, "datalog": bool(self.datalog)}), flush=True)
    return _incumbent(self, prog, tp, fn, tn, fp, size)

Settings.print_incomplete_solution2 = _report_incumbent

def run(kbpath, timeout, noisy, eval_timeout, max_literals):
    settings = Settings(
        kbpath=kbpath,
        noisy=noisy,
        timeout=timeout,
        eval_timeout=eval_timeout,
        max_literals=max_literals,
        solver="rc2",
        quiet=True,
        show_stats=False,
    )
    prog, score, stats = learn_solution(settings)
    rules = []
    if prog:
        for rule in prog:
            head, body = rule
            rules.append(format_prog([(head, body)]).strip())
    
    out = {
        "success": True,
        "rules": rules,
        "score": list(score) if score else None,
        "datalog": bool(settings.datalog),
        "total_time_s": stats.total_exec_time() if hasattr(stats, "total_exec_time") else 0.0,
    }
    print("POPPER_RESULT_JSON:" + json.dumps(out))

if __name__ == "__main__":
    run(sys.argv[1], int(sys.argv[2]), sys.argv[3] == "1", float(sys.argv[4]), int(sys.argv[5]))
"""


INCUMBENT_PREFIX = "POPPER_INCUMBENT_JSON:"


def stopped_result(output: str, elapsed_s: float) -> PopperResult:
    """The result of a worker stopped after its grace: its last reported incumbent.

    With no incumbent reported, the search found nothing to keep and the run fails.
    """
    last = None
    for line in output.splitlines():
        if line.startswith(INCUMBENT_PREFIX):
            last = json.loads(line[len(INCUMBENT_PREFIX):])
    if last is None:
        return PopperResult(
            success=False, rules=[], score=None, datalog=False, total_time_s=elapsed_s,
            raw_output=output, timed_out=True, stopped_by_runner=True,
        )
    return PopperResult(
        success=True,
        rules=last["rules"],
        score=tuple(last["score"]),
        datalog=last["datalog"],
        total_time_s=elapsed_s,
        raw_output=output,
        timed_out=True,
        stopped_by_runner=True,
    )


def popper_version() -> str:
    """Version of the installed Popper, for rule provenance."""
    from importlib.metadata import version

    return version("popper-ilp")


def run_popper(
    kbpath: Path | str,
    timeout: int = DEFAULT_TIMEOUT_S,
    noisy: bool = True,
    eval_timeout: float = DEFAULT_EVAL_TIMEOUT_S,
    max_literals: int = DEFAULT_MAX_LITERALS,
    python_bin: Path | str | None = None,
    grace_s: float = WORKER_GRACE_S,
) -> PopperResult:
    """Run Popper on a knowledge base directory in an isolated, deterministic subprocess."""
    kb_dir = Path(kbpath).resolve()
    if not (kb_dir / "bias.pl").exists() or not (kb_dir / "bk.pl").exists():
        raise FileNotFoundError(f"Directory {kb_dir} must contain bias.pl and bk.pl")

    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "0"
    # Unset repo PYTHONPATH so worker runs with clean environment
    env.pop("PYTHONPATH", None)

    py_exe = str(python_bin or sys.executable)
    cmd = [
        py_exe,
        "-c",
        _WORKER_SCRIPT,
        str(kb_dir),
        str(timeout),
        "1" if noisy else "0",
        str(eval_timeout),
        str(max_literals),
    ]

    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, env=env, capture_output=True, text=True, check=False, timeout=timeout + grace_s
        )
    except subprocess.TimeoutExpired as expired:
        output = expired.stdout or ""
        output = output.decode() if isinstance(output, bytes) else output
        return stopped_result(output, time.monotonic() - started)
    elapsed_s = time.monotonic() - started

    if proc.returncode != 0:
        return PopperResult(
            success=False,
            rules=[],
            score=None,
            datalog=False,
            total_time_s=0.0,
            raw_output=proc.stderr or proc.stdout,
        )

    for line in proc.stdout.splitlines():
        if line.startswith("POPPER_RESULT_JSON:"):
            data = json.loads(line[len("POPPER_RESULT_JSON:") :])
            return PopperResult(
                success=True,
                rules=data["rules"],
                score=tuple(data["score"]) if data["score"] else None,
                datalog=data["datalog"],
                total_time_s=float(data["total_time_s"]),
                raw_output=proc.stdout,
                # Popper returns its best program so far at the budget, so a
                # truncated search still reports success; say so explicitly.
                timed_out=elapsed_s >= timeout,
            )

    return PopperResult(
        success=False,
        rules=[],
        score=None,
        datalog=False,
        total_time_s=0.0,
        raw_output=proc.stdout + "\n" + proc.stderr,
    )

