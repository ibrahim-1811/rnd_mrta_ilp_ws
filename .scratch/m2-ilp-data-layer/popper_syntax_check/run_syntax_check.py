"""Tiny (<2s) standalone subprocess check of Popper v4.4.0 syntax & noisy mode."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _worker(kbpath: str, noisy: bool) -> int:
    from popper.loop import learn_solution
    from popper.util import Settings, format_prog

    settings = Settings(
        kbpath=kbpath,
        noisy=noisy,
        timeout=5,
        solver="rc2",
        quiet=True,
        show_stats=False,
    )
    prog, score, stats = learn_solution(settings)
    out = {
        "noisy": noisy,
        "datalog_inferred": bool(settings.datalog),
        "single_solve": bool(settings.single_solve),
        "pointless_detected": sorted([list(x) for x in settings.pointless]),
        "score_tuple_tp_fn_tn_fp_size": list(score),
        "formatted_prog": format_prog(prog) if prog else None,
    }
    print(json.dumps(out))
    return 0


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        kbpath = sys.argv[2].split("::", 1)[0]
        noisy = sys.argv[2].split("::", 1)[1] == "1"
        return _worker(kbpath, noisy)

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    # 1. Exact noiseless check ( separate process #1 )
    res_exact = subprocess.run(
        [sys.executable, str(__file__), "--worker", f"{HERE}::0"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    print("EXACT_MODE:", res_exact.stdout.strip())

    # 2. Noisy MDL check with 1 corrupted example in a temp copy ( separate process #2 )
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        shutil.copy(HERE / "bk.pl", tmp_path / "bk.pl")
        shutil.copy(HERE / "bias.pl", tmp_path / "bias.pl")
        noisy_exs = (HERE / "exs.pl").read_text() + "pos(assign(d_syn01,r_syn_a,o_syn_y)).\n"
        (tmp_path / "exs.pl").write_text(noisy_exs)
        res_noisy = subprocess.run(
            [sys.executable, str(__file__), "--worker", f"{tmp_path}::1"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        print("NOISY_MODE:", res_noisy.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
