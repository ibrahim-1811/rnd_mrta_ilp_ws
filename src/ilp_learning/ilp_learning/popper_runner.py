"""Isolated subprocess runner for Popper v4.4.0.

Enforces:
1. Subprocess isolation: prevents internal global Clingo state leakage across invocations.
2. Determinism: runs with PYTHONHASHSEED=0 and unsets conflicting PYTHONPATH.
3. Structured output: parses discovered rules, score tuple (TP, FN, TN, FP, size), and timing.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys


@dataclass(frozen=True)
class PopperResult:
    success: bool
    rules: list[str]
    score: tuple[int, int, int, int, int] | None  # (tp, fn, tn, fp, size)
    datalog: bool
    total_time_s: float
    raw_output: str


_WORKER_SCRIPT = """
import json
import sys
from popper.loop import learn_solution
from popper.util import Settings, format_prog

def run(kbpath, timeout, noisy):
    settings = Settings(
        kbpath=kbpath,
        noisy=noisy,
        timeout=timeout,
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
    run(sys.argv[1], int(sys.argv[2]), sys.argv[3] == "1")
"""


def run_popper(
    kbpath: Path | str,
    timeout: int = 15,
    noisy: bool = False,
    python_bin: Path | str | None = None,
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
    ]

    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)

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
            )

    return PopperResult(
        success=False,
        rules=[],
        score=None,
        datalog=False,
        total_time_s=0.0,
        raw_output=proc.stdout + "\n" + proc.stderr,
    )

