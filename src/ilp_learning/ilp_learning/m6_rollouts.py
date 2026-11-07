"""M6-2: counterfactual rollouts on the exact decision states M4 and M5 trained on.

    python -m ilp_learning.m6_rollouts runs/m4_corpus/q3/nearest_astar/r70_s10* \\
        --out runs/m6_rollouts/q3 [--limit N]

For each corpus run (an induction run recorded by run_experiment.py):

1. Rebuild the run from its own run.json, with the builder scripts/m1_probe.py uses:
   same config, map, workload file, fleet, strategy, seed, window and quantum. Each
   input file must still hash to what the manifest recorded.
2. Replay it once and compare the replayed decisions table with the corpus's
   decisions.parquet, every column but run_id (the run_id hashes the git commit, so a
   replay at a later commit has another one). Any difference stops here: rollouts from
   other states would not be the states M4 and M5 learned on.
3. Probe the decisions M4 and M5 can draw examples from (sampler.labelled_decisions),
   all of them or --limit evenly spaced ones: the teacher's pivot pair and up to three
   alternatives for the same robot, H = 120 s, one exact clone check
   (simulation_env/rollout.py).

Output, per run, in <out>/r<robots>_s<seed>/: rollouts.csv, probe.json (names the corpus
run, records the alignment check), replay/ (the replayed tables, the evidence). The
corpus is only read.

--arm m6a (default) is the M6-A probe above. --arm mp1 is the multi-pair arm: at each
probed decision, up to --max-actions pairs across robots and orders from
ilp_learning.m6_candidates (behaviour pair, panel proposals at the same state, an
alternative robot, an alternative order, a seeded random control), each forced by
rollout.ForcedPair; rows also carry every window order's completion time. mp1 needs
the run's teacher_labels.parquet. probe.json records each decision's evaluated order
set A(d), fixed at planning time, for the jw1 objective (supervision/outcome_common.py);
outcome_aware (frozen J) refuses mp1 rollouts.

--continuations P1,P2,... (mp1 only) rolls every candidate out once per listed policy:
the forced decision is the behaviour policy's with the pair forced, identical for all,
and every later decision is that policy's. Default: the behaviour policy only.

--sample stratified --quota K draws K decisions per stratum plus K random controls
(ilp_learning.m6_sampling) instead of --limit's even spacing. --shard I/N probes every
N-th sampled decision starting at I, so one run's probes can be split across jobs; the
sample itself does not depend on the shard.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pandas as pd

from ilp_learning.bk_builder import derive_est_finish_s
from ilp_learning.m6_candidates import CANDIDATE_SET, DEFAULT_MAX_ACTIONS, select_candidates
from ilp_learning.m6_sampling import coverage, decision_strata, sample_decisions
from ilp_learning.supervision.outcome_common import evaluated_orders
from ilp_learning.sampler import DEFAULT_CORPUS_SEED, labelled_decisions
from ilp_learning.supervision.outcome_aware import FROZEN
from simulation_env.dataset import sha256_of
from simulation_env.rollout import decision_ticks, probe, probe_pairs, tables, verified
from simulation_env.seed_split import check_run_is_induction, workload_seed_of


REPO = Path(__file__).resolve().parents[3]
PROBE_SCRIPT = REPO / "src/simulation_env/scripts/m1_probe.py"
ALTERNATIVES = 3
NOT_STATE = ("run_id",)
ARMS = ("m6a", CANDIDATE_SET)


class Progress:
    """Live status lines on stdout (flushed): replay heartbeat, one line per branch, ETA."""

    def __init__(self, enabled: bool = True, every_s: float = 15.0) -> None:
        self.enabled, self.every_s = enabled, every_s
        self.started = time.perf_counter()
        self._last = 0.0
        self._branch_started = None

    def say(self, text: str) -> None:
        if self.enabled:
            elapsed = time.perf_counter() - self.started
            print(f"[{elapsed / 60:5.1f} min] {text}", flush=True)

    def replay(self, executor, total_orders: int) -> None:
        now = time.perf_counter()
        if not self.enabled or now - self._last < self.every_s:
            return
        self._last = now
        load = executor.decision_load(0, 0)
        self.say(
            f"replay  sim t={float(executor.engine.time):7.0f} s | orders done "
            f"{len(executor.result.completions)}/{total_orders} | robots busy {load['robots_busy']}, "
            f"charging {load['robots_charging']} | decisions {executor.result.decisions}"
        )

    def branch(self, done: int, total: int, row: dict) -> None:
        if not self.enabled:
            return
        if self._branch_started is None:
            self._branch_started = time.perf_counter() - float(row.get("wall_s", 0.0))
        per = (time.perf_counter() - self._branch_started) / done
        window = row.get("window_completion_s") or {}
        flow = sum(window.values()) if isinstance(window, dict) else float("nan")
        swap = f" + swap {row['swap']}" if row.get("swap") else ""
        self.say(
            f"branch {done:>3}/{total} | decision {row['decision_id']} {row.get('candidate_sources', '')} | "
            f"{row.get('forced_robot')} -> {row.get('forced_order')}{swap} | then {row['continuation']} | "
            f"{row.get('rollout_status')} | window orders done {len(window)} | flow {flow:,.0f} s, "
            f"waiting(120 s) {float(row.get('waiting_in_h_s', float('nan'))):,.0f} s | "
            f"{float(row.get('wall_s', 0)):.0f} s | ETA {per * (total - done) / 60:.0f} min"
        )


class AlignmentError(RuntimeError):
    """The replay is not the corpus run; no rollout may be taken from it."""


def _m1_probe():
    scripts = str(PROBE_SCRIPT.parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location("m1_probe", PROBE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolve(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else REPO / candidate


def corpus_builder(manifest: dict):
    """A function that builds the corpus run afresh, checked against its manifest."""
    for key, path_key in (("config_sha256", "config_path"), ("map_sha256", "map_path"),
                          ("orders_sha256", "orders_path")):
        if sha256_of(_resolve(manifest[path_key])) != manifest[key]:
            raise AlignmentError(f"{manifest[path_key]} no longer hashes to the corpus's {key}")

    args = SimpleNamespace(
        config=str(_resolve(manifest["config_path"])),
        robots=int(manifest["robot_count"]),
        orders=int(manifest["order_limit"] or 0),
        window=manifest["allocation_window"],
        quantum=float(manifest["dispatch_quantum_s"]),
        seed=int(manifest["seed"]),
        strategy=manifest["strategy"],
    )
    build, _seed = _m1_probe().builder(args, _resolve(manifest["orders_path"]))

    def checked():
        executor = build()
        rebuilt = executor.manifest
        for key in ("strategy_sha256", "orders", "line_items", "window_width", "speed_mps", "battery"):
            if rebuilt[key] != manifest[key]:
                raise AlignmentError(f"rebuilt run differs in {key}: {rebuilt[key]} != {manifest[key]}")
        return executor

    return checked


def compare_decisions(replayed: pd.DataFrame, corpus: pd.DataFrame) -> list[str]:
    """Columns (or shape) in which the replay differs from the corpus; empty = identical."""
    left = replayed.drop(columns=list(NOT_STATE)).reset_index(drop=True)
    right = corpus.drop(columns=list(NOT_STATE)).reset_index(drop=True)
    if list(left.columns) != list(right.columns):
        return ["columns"]
    if len(left) != len(right):
        return [f"rows {len(left)} != {len(right)}"]
    return [c for c in left.columns if not left[c].equals(right[c])]


def evenly_spaced(pool: list[int], limit: int | None) -> list[int]:
    if not limit or limit >= len(pool):
        return list(pool)
    step = len(pool) / limit
    return [pool[int(step * k + step / 2)] for k in range(limit)]


def pair_plan(corpus_dir: Path, manifest: dict, corpus: pd.DataFrame, decisions: list[int],
              max_actions: int, seed: int) -> dict:
    """{decision_id: [(robot_id, order_id, provenance)]} for the mp1 arm."""
    panel_path = corpus_dir / "teacher_labels.parquet"
    if not panel_path.exists():
        raise AlignmentError(f"{corpus_dir}: --arm {CANDIDATE_SET} needs teacher_labels.parquet")
    panel = pd.read_parquet(panel_path)
    frame = corpus.assign(est_finish_s=derive_est_finish_s(corpus, manifest))
    plan = {}
    for decision_id in decisions:
        actions = select_candidates(
            frame[frame["decision_id"] == decision_id],
            panel[panel["decision_id"] == decision_id],
            run_id=manifest["run_id"], seed=seed, behaviour=manifest["strategy"],
            max_actions=max_actions,
        )
        plan[decision_id] = [(a.robot_id, a.order_id, a.extra(k)) for k, a in enumerate(actions)]
    return plan


def rollouts_for_run(corpus_dir: Path, out_root: Path, limit: int | None, verify: int = 1,
                     arm: str = "m6a", max_actions: int = DEFAULT_MAX_ACTIONS,
                     seed: int = DEFAULT_CORPUS_SEED, sample: str = "even", quota: int = 2,
                     shard: tuple[int, int] = (0, 1),
                     continuations: tuple[str, ...] | None = None,
                     progress: Progress | None = None) -> dict:
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; known: {ARMS}")
    if continuations and arm == "m6a":
        raise ValueError("continuation policies are for the multi-pair arm only")
    manifest = json.loads((corpus_dir / "run.json").read_text(encoding="utf-8"))
    check_run_is_induction(manifest)
    workload = workload_seed_of(manifest["orders_path"])
    index, shards = shard
    if not 0 <= index < shards:
        raise ValueError(f"shard {index}/{shards} is out of range")
    suffix = f"_shard{index}of{shards}" if shards > 1 else ""
    out = out_root / f"r{manifest['robot_count']}_s{workload}{suffix}"
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"{out} is not empty")
    out.mkdir(parents=True, exist_ok=True)

    build = corpus_builder(manifest)
    corpus = pd.read_parquet(corpus_dir / "decisions.parquet")
    progress = progress or Progress(enabled=False)
    progress.say(
        f"{corpus_dir} | {manifest['robot_count']} robots, {manifest['orders']} orders, "
        f"seed {workload} | arm {arm} | shard {index}/{shards} | output {out}"
    )
    progress.say("replay: re-running the recorded run to prove the state is identical")

    started = time.perf_counter()
    first, made_in = decision_ticks(build, lambda ex: progress.replay(ex, int(manifest["orders"])))
    replay_dir = first.recorder.write(out / "replay", first.manifest)
    replay_s = time.perf_counter() - started
    differs = compare_decisions(pd.read_parquet(Path(replay_dir) / "decisions.parquet"), corpus)
    if differs:
        raise AlignmentError(f"{corpus_dir}: replay differs from the corpus in {differs}")
    progress.say(f"replay identical to the recorded run ({len(corpus)} candidate rows) in {time.perf_counter() - started:.0f} s")

    pool = [d for d in labelled_decisions(corpus) if d in made_in and made_in[d] > 1]
    sampling = None
    if sample == "stratified":
        features_path = corpus_dir / "features.parquet"
        features = pd.read_parquet(features_path) if features_path.exists() else None
        table = decision_strata(corpus, features, pool)
        drawn = sample_decisions(table, quota=quota, seed=seed, run_id=manifest["run_id"])
        chosen = [d.decision_id for d in drawn]
        sampling = {
            "method": "stratified", "quota": quota, "seed": seed,
            "features": features is not None,
            "coverage": coverage(table, drawn),
            "drawn": [{"decision_id": d.decision_id, "drawn_from": d.drawn_from,
                       "p_select": d.p_select, "strata": list(d.strata)} for d in drawn],
        }
    elif sample == "even":
        chosen = evenly_spaced(pool, limit)
    else:
        raise ValueError(f"unknown sample method {sample!r}")
    chosen = chosen[index::shards]
    progress.say(f"this shard probes {len(chosen)} decisions: {chosen}")

    started = time.perf_counter()
    if arm == "m6a":
        rows, checks = probe(
            build, chosen, made_in, alternatives=ALTERNATIVES, horizon_s=FROZEN.horizon_s,
            verify=verify, baseline=tables(first),
        )
        planned = len(chosen) * (ALTERNATIVES + 1)
        a_sets = None
    else:
        plan = pair_plan(corpus_dir, manifest, corpus, chosen, max_actions, seed)
        a_sets = {
            str(d): evaluated_orders(corpus[corpus["decision_id"] == d],
                                     [(r, o) for r, o, _ in plan[d]])
            for d in plan
        }
        rows, checks = probe_pairs(
            build, plan, made_in, horizon_s=FROZEN.horizon_s, verify=verify,
            baseline=tables(first), continuations=tuple(continuations or (None,)),
            seed=int(manifest["seed"]), on_branch=progress.branch, on_stage=progress.say,
        )
        for row in rows:
            row["window_completion_s"] = json.dumps(row["window_completion_s"], sort_keys=True)
        planned = sum(len(actions) for actions in plan.values()) * len(continuations or (None,))
    rollout_s = time.perf_counter() - started

    frame = pd.DataFrame(rows)
    frame.to_csv(out / "rollouts.csv", index=False)
    report = {
        "run_id": manifest["run_id"],
        "corpus_dir": str(corpus_dir),
        "replay_run_id": first.manifest["run_id"],
        "replay_identical": True,
        "replay_compared_columns": [c for c in corpus.columns if c not in NOT_STATE],
        "robots": int(manifest["robot_count"]),
        "workload_seed": workload,
        "quantum": float(manifest["dispatch_quantum_s"]),
        "labelled_decisions": len(labelled_decisions(corpus)),
        "probe_pool": len(pool),
        "rollout_decisions": chosen,
        "rollout_branches": len(rows),
        "rollout_branches_unavailable": planned - len(rows),
        "sampling": sampling,
        "shard": [index, shards],
        "evaluated_orders": a_sets,
        "arm": arm,
        "candidate_seed": seed if arm != "m6a" else None,
        "max_actions": max_actions if arm != "m6a" else None,
        "continuations": list(continuations) if continuations else [manifest["strategy"]],
        "clone_checks": checks,
        "clone_verified": verified(checks),
        "args": {"horizon": str(FROZEN.horizon_s), "alternatives": ALTERNATIVES, "limit": limit},
        "replay_wall_s": replay_s,
        "rollout_wall_s": rollout_s,
        "rollouts_sha256": sha256_of(out / "rollouts.csv"),
    }
    (out / "probe.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    if not report["clone_verified"]:
        raise AlignmentError(f"{corpus_dir}: clone check failed; rollouts are not evidence")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="+", type=Path, help="corpus run directories")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=None, help="probe at most N decisions per run")
    parser.add_argument("--arm", choices=ARMS, default="m6a")
    parser.add_argument("--max-actions", type=int, default=DEFAULT_MAX_ACTIONS)
    parser.add_argument("--sample", choices=("even", "stratified"), default="even")
    parser.add_argument("--quota", type=int, default=2, help="decisions per stratum (stratified)")
    parser.add_argument("--shard", default="0/1", help="I/N: probe every N-th sampled decision from I")
    parser.add_argument("--quiet", action="store_true", help="no live progress lines")
    parser.add_argument("--continuations", default=None,
                        help="mp1: comma-separated continuation policies, e.g. nearest_astar,multi_criteria,auction")
    args = parser.parse_args()
    index, shards = (int(x) for x in args.shard.split("/"))

    for run in args.runs:
        r = rollouts_for_run(run, args.out, args.limit, arm=args.arm, max_actions=args.max_actions,
                             sample=args.sample, quota=args.quota, shard=(index, shards),
                             continuations=tuple(args.continuations.split(",")) if args.continuations else None,
                             progress=Progress(enabled=not args.quiet))
        print(
            f"{run}: replay identical, {len(r['rollout_decisions'])}/{r['probe_pool']} decisions, "
            f"{r['rollout_branches']} branches, clone verified {r['clone_verified']}, "
            f"{r['rollout_wall_s']:.1f} s"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
