"""M6-1 profile: what outcome supervision costs, and how much of it there is.

    python -m ilp_learning.m6_profile --out runs/m6_profile

For each fleet size (default 70 and 100), one nearest_astar probe on one induction
seed (default 104), quantum 3, 300 orders, the M4/M5 state policy. --decisions evenly
spaced decisions are branched: the teacher's pair and up to three alternatives, H=120 s,
one clone check (scripts/m1_probe.py). The rollouts are then labelled by
supervision/outcome_aware.py and the profile reports:

    time per rollout     mean wall time of one branch, and the whole probe's wall
                         time per branch (pass 1, the clone check and copies included)
    label yield          pos / neg / abstain / excluded branches, and why
    decision coverage    probed decisions over the run's choice-bearing decisions

A profile, not the M6 corpus: nothing here is trained on.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

import pandas as pd

from ilp_learning.sampler import choice_pairs
from ilp_learning.supervision.outcome_aware import FROZEN, outcome_labels, summarize, write_labels
from simulation_env.seed_split import check_induction_purpose


REPO = Path(__file__).resolve().parents[3]
PROBE = REPO / "src/simulation_env/scripts/m1_probe.py"


def choice_bearing_decisions(decisions: pd.DataFrame) -> int:
    eligible = decisions[decisions["eligible"]]
    return sum(1 for _, rows in eligible.groupby("decision_id") if len(choice_pairs(rows)))


def profile_one(out: Path, robots: int, seed: int, decisions: int, orders: int, quantum: float) -> dict:
    run_dir = out / f"r{robots}_q{quantum:g}_s{seed}"
    command = [
        sys.executable, str(PROBE), "--out", str(run_dir),
        "--robots", str(robots), "--orders", str(orders),
        "--workload-seed", str(seed), "--quantum", str(quantum),
        "--rollouts", str(decisions), "--alternatives", "3",
        "--horizon", str(FROZEN.horizon_s), "--verify", "1",
    ]
    started = time.perf_counter()
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
    wall = time.perf_counter() - started

    probe = json.loads((run_dir / "probe.json").read_text())
    rollouts = pd.read_csv(run_dir / "rollouts.csv")
    labels = outcome_labels(run_dir)
    labels_sha = write_labels(labels, run_dir / "outcome_labels.csv")
    table = pd.read_parquet(run_dir / "decisions.parquet")
    choice = choice_bearing_decisions(table)
    summary = summarize(labels)

    return {
        "run_dir": str(run_dir),
        "robots": robots,
        "seed": seed,
        "orders": orders,
        "quantum": quantum,
        "run_wall_s": probe["run_wall_s"],
        "rollout_wall_s": probe["rollout_wall_s"],
        "profile_wall_s": wall,
        "branches": len(rollouts),
        "branches_unavailable": probe["rollout_branches_unavailable"],
        "mean_branch_wall_s": float(rollouts["wall_s"].mean()),
        "max_branch_wall_s": float(rollouts["wall_s"].max()),
        "probe_wall_per_branch_s": probe["rollout_wall_s"] / max(len(rollouts), 1),
        "clone_verified": probe["clone_verified"],
        "decisions_in_run": int(table["decision_id"].nunique()),
        "choice_bearing_decisions": choice,
        "decision_coverage": summary["probed_decisions"] / choice if choice else float("nan"),
        "labels_sha256": labels_sha,
        **summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--robots", type=int, nargs="+", default=[70, 100])
    parser.add_argument("--seed", type=int, default=104)
    parser.add_argument("--decisions", type=int, default=20)
    parser.add_argument("--orders", type=int, default=300)
    parser.add_argument("--quantum", type=float, default=3.0)
    args = parser.parse_args()

    check_induction_purpose([args.seed])
    if args.out.exists() and any(args.out.iterdir()):
        parser.error(f"{args.out} is not empty; choose a new --out")
    args.out.mkdir(parents=True, exist_ok=True)

    results = [
        profile_one(args.out, robots, args.seed, args.decisions, args.orders, args.quantum)
        for robots in args.robots
    ]
    (args.out / "profile.json").write_text(json.dumps(results, indent=2))

    for r in results:
        print(
            f"r{r['robots']}: {r['branches']} branches, mean {r['mean_branch_wall_s']:.2f} s/branch "
            f"(max {r['max_branch_wall_s']:.2f}), probe {r['probe_wall_per_branch_s']:.2f} s/branch; "
            f"pos {r['pos']} neg {r['neg']} abstain {r['abstain']} excluded {r['excluded']}; "
            f"decisions with an example {r['decisions_with_example']}/{r['probed_decisions']}; "
            f"coverage {r['probed_decisions']}/{r['choice_bearing_decisions']} choice-bearing"
        )
    print(f"profile: {args.out / 'profile.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
