#!/usr/bin/env python3
"""Report how strongly the continuation policy changes M6-MP outcome labels.

    pixi run python scripts/m6_continuation_report.py RUN_DIR ROLLOUT_ROOT [RUN_DIR ROLLOUT_ROOT ...]

RUN_DIR is a corpus run; ROLLOUT_ROOT holds its mp1 shard directories, rolled out with
--continuations. Prints, per run: replay and clone checks, branch cost, cross-policy
label counts (conservative and m6_1), best-branch agreement, rank correlation and
regret between policies, and each policy's own labels. Reads only; writes nothing.
"""

import glob
import json
import sys

import pandas as pd

from ilp_learning.supervision.outcome_common import JW2, cross_policy_labels, cross_policy_report


def main(argv):
    for run, root in zip(argv[0::2], argv[1::2]):
        dirs = [p.rsplit("/", 1)[0] for p in sorted(glob.glob(f"{root}/*/probe.json"))]
        probes = [json.load(open(f"{d}/probe.json")) for d in dirs]
        rolls = pd.concat([pd.read_csv(f"{d}/rollouts.csv") for d in dirs])
        print(f"\n===== {run}  ({len(dirs)} shards)")
        print("continuations:", sorted({c for p in probes for c in p["continuations"]}))
        print("replay identical:", all(p["replay_identical"] for p in probes),
              "| clone verified:", all(p["clone_verified"] for p in probes),
              "| unavailable branches:", sum(p["rollout_branches_unavailable"] for p in probes))
        print("coverage:", probes[0]["sampling"]["coverage"])
        cost = rolls.groupby("continuation")["wall_s"].agg(["count", "mean", "max"]).round(1)
        print("branch wall time by policy (s):\n" + cost.to_string())
        for mode in ("conservative", "m6_1"):
            labels = cross_policy_labels(run, dirs, mode=mode, config=JW2)
            report = cross_policy_report(labels)
            print(f"\n[{mode}]", json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main(sys.argv[1:])
