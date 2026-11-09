"""Oracle rollout policy: how much better can a rollout-guided choice make a run?

The upper bound for outcome-aware rules. The behaviour policy (a teacher) drives the
run; at every decision the state just before it is cloned, each mp1 candidate pair of
that decision is forced in its own branch and continued by the behaviour policy for the
jw2 horizon, and the decision takes the pair with the decisive best jw2 score (the same
labels group D learns from: outcome_common.label_decision, conservative mode). The run
then continues from that choice and the next decision is treated the same way. This is
one-step rollout policy improvement (Bertsekas): no rule, no learning, only simulation.

    python -m ilp_learning.m6_oracle RUN_DIR --out DIR [--max-decisions N]

RUN_DIR is a recorded corpus run; its manifest rebuilds the identical workload. The
output holds oracle.json (both runs' metrics, every decision's verdict) and the
oracle's decision and outcome tables.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

from ilp_learning.m6_candidates import DEFAULT_MAX_ACTIONS, select_candidates
from ilp_learning.m6_rollouts import corpus_builder
from ilp_learning.supervision.outcome_common import JW2, label_decision, window_orders
from simulation_env.rollout import ForcedPair, _Stop, branch_pair, clone
from simulation_env.workload_executor import PerfBudget, WorkloadStalledError

ORACLE_SEED = 20261109


def _metrics(executor) -> dict:
    outcomes = pd.DataFrame(executor.recorder.outcomes)
    totals = executor.totals()
    return {
        "orders_completed": int(len(outcomes)),
        "order_makespan_s": float(outcomes["completed_at"].max()) if len(outcomes) else float("nan"),
        "mean_flow_s": float((outcomes["completed_at"] - outcomes["launched_at"]).mean()) if len(outcomes) else float("nan"),
        "waiting_time_s": float(totals["waiting_time_s"]),
        "distance_m": float(totals["distance_m"]),
        "decisions": int(executor.result.decisions),
    }


def _run_to_end(executor, stop) -> bool:
    """Run (or resume) until the end or until stop() asks to; True when finished."""

    def tick(sim_time, batch):
        if stop(executor):
            raise _Stop

    executor._external_tick = tick
    try:
        if getattr(executor, "_oracle_started", False):
            # Exactly rollout.resume, without swallowing the stop.
            executor.engine.run()
            while not executor.finished():
                executor._perf = PerfBudget(executor.engine.sim_time)
                try:
                    recovered = executor._recover()
                finally:
                    executor._close_perf()
                if not recovered:
                    raise WorkloadStalledError("oracle run stalled")
                executor.dispatch()
                executor.engine.run()
        else:
            executor._oracle_started = True
            executor.run()
    except _Stop:
        return False
    return executor.finished()


def _advance(executor, ticks: int) -> bool:
    """Run exactly this many more ticks (or to the end); True when finished."""
    count = {"n": 0}

    def stop(_):
        count["n"] += 1
        return count["n"] >= ticks

    return _run_to_end(executor, stop)


def _next_decision(executor):
    """(ticks until the next decision is made, its rows) from a scout copy, or None."""
    after = executor.result.decisions
    scout = clone(executor, rows=False)
    count = {"n": 0}

    def stop(x):
        count["n"] += 1
        return x.result.decisions > after

    finished = _run_to_end(scout, stop)
    if finished and scout.result.decisions <= after:
        return None
    rows = pd.DataFrame([r for r in scout.recorder.decisions if r["decision_id"] == after])
    return count["n"], rows


def oracle_run(build, *, run_id: str, behaviour: str, horizon_s: float = JW2.horizon_s,
               max_decisions: int | None = None, max_actions: int = DEFAULT_MAX_ACTIONS,
               progress=None) -> tuple[object, list[dict]]:
    """The run with every decision replaced by its rollout-best pair; (executor, verdicts).

    A scout copy finds when the next decision is made; the run is advanced to the tick
    before it, the candidates are branched from that state, and the decision is made
    with the best pair forced (rollout.ForcedPair) or left to the behaviour policy.
    Pausing and resuming a run replays it exactly (checked in the tests).
    """
    executor = build()
    verdicts: list[dict] = []
    started = time.perf_counter()
    finished = _advance(executor, 1)  # decision 0 is made before the first tick
    while not finished:
        if max_decisions is not None and len(verdicts) >= max_decisions:
            finished = _run_to_end(executor, lambda _: False)
            break
        found = _next_decision(executor)
        if found is None:
            finished = _run_to_end(executor, lambda _: False)
            break
        ticks, rows = found
        if ticks > 1 and _advance(executor, ticks - 1):
            break
        decision_id = executor.result.decisions
        candidates = select_candidates(rows, None, run_id=run_id, seed=ORACLE_SEED,
                                       behaviour=behaviour, max_actions=max_actions)
        verdict = {"decision_id": decision_id, "t": float(executor.engine.time), "candidates": len(candidates)}
        if len(candidates) >= 2:
            branches = []
            for rank, c in enumerate(candidates):
                result = branch_pair(executor, decision_id, c.robot_id, c.order_id, horizon_s)
                if result is None:
                    continue
                measures, decided = result
                branches.append({
                    "decision_id": decision_id, "branch": f"c{rank}", "candidate_rank": rank,
                    "candidate_sources": "+".join(c.sources), "candidate_p_select": c.p_select,
                    "forced": decided.get("forced"), "forced_robot": c.robot_id,
                    "forced_order": c.order_id, "swap": decided.get("swap", ""),
                    "waiting_in_h_s": measures["waiting_in_h_s"],
                    "rollout_status": measures["rollout_status"],
                    "window_completion_s": json.dumps(measures["window_completion_s"]),
                })
            labelled = label_decision(pd.DataFrame(branches), window_orders(rows), JW2, "conservative")
            best = labelled[labelled["label"] == "pos"]
            teacher = labelled[labelled["candidate_sources"].str.split("+").map(lambda s: "behaviour" in s)]
            verdict["teacher_j_w_s"] = float(teacher["j_w_s"].iloc[0]) if len(teacher) else float("nan")
            if len(best) and len(teacher) and best.index[0] != teacher.index[0]:
                row = best.iloc[0]
                pick = candidates[int(row["candidate_rank"])]
                verdict.update(verdict="override", pair=f"{pick.robot_id}:{pick.order_id}",
                               best_j_w_s=float(row["j_w_s"]),
                               gain_s=float(verdict["teacher_j_w_s"] - row["j_w_s"]))
                executor.allocator = ForcedPair(executor.allocator, executor, decision_id,
                                                pick.robot_id, pick.order_id)
            else:
                verdict["verdict"] = "keep"
        else:
            verdict["verdict"] = "no_choice"
        verdicts.append(verdict)
        if progress:
            progress(verdict, time.perf_counter() - started)
        finished = _advance(executor, 1)
        if isinstance(executor.allocator, ForcedPair):
            executor.allocator = executor.allocator.inner
    return executor, verdicts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path, help="recorded corpus run directory")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--max-decisions", type=int, default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    manifest = json.loads((args.run / "run.json").read_text(encoding="utf-8"))
    build = corpus_builder(manifest)
    behaviour = manifest["strategy"]
    label = f"{behaviour} r{manifest['robot_count']}"

    teacher = build()
    teacher.run()
    baseline = _metrics(teacher)
    print(f"[{label}] teacher replay: makespan {baseline['order_makespan_s']:.1f} s, "
          f"{baseline['decisions']} decisions", flush=True)

    def progress(v, elapsed):
        if not args.quiet:
            extra = f" -> {v['pair']} gains {v['gain_s']:.0f} s" if v["verdict"] == "override" else ""
            print(f"[{label} {elapsed / 60:5.1f} min] decision {v['decision_id']:3d} t={v['t']:6.1f} s "
                  f"{v['verdict']}{extra}", flush=True)

    executor, verdicts = oracle_run(build, run_id=manifest["run_id"], behaviour=behaviour,
                                    max_decisions=args.max_decisions, progress=progress)
    oracle = _metrics(executor)
    args.out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(executor.recorder.decisions).to_parquet(args.out / "decisions.parquet")
    pd.DataFrame(executor.recorder.outcomes).to_parquet(args.out / "outcomes.parquet")
    report = {
        "corpus_run": str(args.run), "run_id": manifest["run_id"], "behaviour": behaviour,
        "robots": manifest["robot_count"], "workload_seed": manifest.get("orders_path"),
        "objective": "jw2", "mode": "conservative", "horizon_s": JW2.horizon_s,
        "max_decisions": args.max_decisions, "teacher": baseline, "oracle": oracle,
        "overrides": sum(v.get("verdict") == "override" for v in verdicts),
        "kept": sum(v.get("verdict") == "keep" for v in verdicts),
        "verdicts": verdicts,
    }
    (args.out / "oracle.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    d = oracle["order_makespan_s"] - baseline["order_makespan_s"]
    print(f"[{label}] oracle makespan {oracle['order_makespan_s']:.1f} s vs teacher "
          f"{baseline['order_makespan_s']:.1f} s ({d:+.1f} s); overrides {report['overrides']}, "
          f"kept {report['kept']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
