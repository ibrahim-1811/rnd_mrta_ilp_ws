"""Oracle rollout policy: how much better can a rollout-guided choice make a run?

The upper bound for outcome-aware rules. The behaviour policy (a teacher) drives the
run; at every decision the state just before it is cloned, each mp1 candidate pair of
that decision is forced in its own branch and continued by the behaviour policy for the
jw2 horizon, and the decision takes the pair with the decisive best jw2 score (the same
labels group D learns from: outcome_common.label_decision, conservative mode). The run
then continues from that choice and the next decision is treated the same way. This is
one-step rollout policy improvement (Bertsekas): no rule, no learning, only simulation.

    python -m ilp_learning.m6_oracle RUN_DIR --out DIR [--objective jw2|wholerun|horizon]
        [--continuations NAME [NAME ...]]

How a branch is judged (--objective):
  jw2       the group-D labels: window orders' flow + 0.5 x fleet waiting over 120 s, the
            branch running until every window order is done (outcome_common.label_decision)
  wholerun  every branch runs to the end of the workload; the best has the lowest order
            makespan, ties within delta broken by the sum of completion times. The exact
            rollout objective (Bertsekas' improvement property needs it); affordable at
            300 orders, not at 1500 or more
  horizon   every branch stops exactly H s after the decision; each window order costs its
            completion time if done, else H plus its best static finish estimate at the
            decision (an optimistic remaining time), plus 0.5 x fleet waiting in H.
            Approximate, but its cost does not grow with the workload (1500, 5000 orders)
A branch beats the teacher's branch only by at least delta (2 s), as the labels require.

Robust labels (--continuations, wholerun or horizon; DECISIONS.md "Robust Oracle Labels
and the Combined Campaign"): every candidate, the teacher's pair included, is rolled out
once per continuation policy (the policy deciding everything after the forced pair; the
behaviour's own name is the behaviour policy itself). The candidate with the best mean
score is taken only if it beats the teacher's pair under every continuation, so a pair
that is lucky in one future is not a label. One continuation is the plain oracle.

RUN_DIR is a recorded corpus run; its manifest rebuilds the identical workload. The
output holds oracle.json (both runs' metrics, every decision's verdict) and the
oracle's run as a run folder train_rules can read (Gate 2, DECISIONS.md "Gate 2 Pass
Criterion and Order"): run.json, decisions.parquet (selected = the pairs the oracle's
run made), outcomes.parquet, features.parquet and teacher_labels.parquet (every panel
teacher's picks at the oracle's own decisions, the same-state imitation control). The
features and the panel come from one replay of the oracle's run with its overrides
forced, which must reproduce the oracle's decision and outcome tables exactly.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import sys
import time
from pathlib import Path

import pandas as pd

from ilp_learning.bk_builder import derive_est_finish_s
from ilp_learning.m6_candidates import DEFAULT_MAX_ACTIONS, select_candidates
from ilp_learning.m6_rollouts import AlignmentError, corpus_builder
from ilp_learning.supervision.outcome_common import JW2, label_decision, window_orders
from simulation_env.congestion import FeatureObserver
from simulation_env.rollout import (
    ForcedPair, _Stop, _fleet_waiting, branch_pair, clone, continuation_policy, resume, tables,
)
from simulation_env.teacher_panel import PANEL_FILE, TeacherPanel
from simulation_env.workload_executor import PerfBudget, WorkloadStalledError

ORACLE_SEED = 20261109
OBJECTIVES = ("jw2", "wholerun", "horizon")
DELTA_S = JW2.delta_s


def _forced_branch(base, decision_id, robot_id, order_id, stop_after_s, continuation=None):
    """A ForcedPair branch from a copy of base, run to the end or for stop_after_s after the decision.

    continuation: a strategy instance deciding every decision after the forced one
    (None: the behaviour policy, as in the plain oracle).
    """
    rollout = clone(base, rows=False)
    forced = ForcedPair(rollout.allocator, rollout, decision_id, robot_id, order_id,
                        continuation=continuation)
    rollout.allocator = forced
    state = {}

    def stop(e):
        decided = forced.decided
        if decided is None or stop_after_s is None:
            return False
        if float(e.engine.time) >= decided["t"] + stop_after_s:
            state.setdefault("waiting_end", _fleet_waiting(e))
            return True
        return False

    try:
        resume(rollout, stop)
        status = "complete"
    except WorkloadStalledError:
        status = "stalled"
    return rollout, forced.decided, status, state


def _score_wholerun(rollout, decided):
    done = [row["completed_at"] for row in rollout.recorder.outcomes]
    return (max(done) if done else float("inf"), float(sum(done)))


def _score_horizon(rollout, decided, rows, est, horizon_s, state):
    t = decided["t"]
    done = {row["order_id"]: row["completed_at"] - t for row in rollout.recorder.outcomes}
    window = window_orders(rows)
    cost = sum(done[o] if o in done else horizon_s + est.get(o, horizon_s) for o in window)
    waiting = state.get("waiting_end", _fleet_waiting(rollout)) - decided["waiting_at_decision"]
    return cost + JW2.lambda_wait * waiting


def _beats(best: tuple, teacher: tuple, objective: str) -> bool:
    """best is decisively better than teacher: lower by delta, or (wholerun) an equal
    makespan with a sum of completion times lower by delta."""
    (bm, bf), (tm, tf) = best, teacher
    return bm <= tm - DELTA_S or (objective == "wholerun" and bm <= tm + 1e-9 and bf <= tf - DELTA_S)


def _mean_score(scores: list[tuple]) -> tuple:
    """Componentwise mean of one candidate's scores over the continuations."""
    return tuple(sum(s[i] for s in scores) / len(scores) for i in range(len(scores[0])))


def robust_choice(scores: dict[int, list[tuple]], teacher_rank: int | None, objective: str):
    """(rank to take or None, best rank or None) from rank -> one score per continuation.

    Only candidates scored under every continuation take part. The best mean score
    (ties: lower rank) is taken when it is not the teacher's and beats the teacher's
    pair under each continuation; with one continuation this is the plain oracle.
    """
    if not scores:
        return None, None
    best = min(scores, key=lambda rank: (_mean_score(scores[rank]), rank))
    if teacher_rank is None or teacher_rank not in scores or best == teacher_rank:
        return None, best
    beats = all(_beats(b, t, objective) for b, t in zip(scores[best], scores[teacher_rank]))
    return (best if beats else None), best


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
               progress=None, objective: str = "jw2", manifest: dict | None = None,
               continuations: tuple = (None,), seed: int | None = None
               ) -> tuple[object, list[dict]]:
    """The run with every decision replaced by its rollout-best pair; (executor, verdicts).

    A scout copy finds when the next decision is made; the run is advanced to the tick
    before it, the candidates are branched from that state, and the decision is made
    with the best pair forced (rollout.ForcedPair) or left to the behaviour policy.
    Pausing and resuming a run replays it exactly (checked in the tests).

    continuations: policy names for the robust labels (None or the behaviour's own name:
    the behaviour policy); the default (None,) is the plain oracle.
    """
    if objective not in OBJECTIVES:
        raise ValueError(f"unknown objective {objective!r}; known: {OBJECTIVES}")
    if objective == "horizon" and manifest is None:
        raise ValueError("the horizon objective needs the run manifest for finish estimates")
    continuations = tuple(None if name in (None, behaviour) else name for name in continuations)
    if not continuations:
        raise ValueError("at least one continuation is needed")
    robust = len(continuations) > 1
    if robust and objective == "jw2":
        raise ValueError("robust continuations need --objective wholerun or horizon")
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
        if len(candidates) >= 2 and objective != "jw2":
            est = {}
            if objective == "horizon":
                eligible = rows[rows["eligible"]]
                est = (eligible.assign(f=derive_est_finish_s(eligible, manifest))
                       .groupby("order_id")["f"].min().to_dict())
            scores: dict[int, list[tuple]] = {}
            for rank, c in enumerate(candidates):
                per = []
                for name in continuations:
                    rollout, decided, status, state = _forced_branch(
                        executor, decision_id, c.robot_id, c.order_id,
                        None if objective == "wholerun" else horizon_s,
                        continuation_policy(name, seed))
                    if decided is None or status != "complete":
                        break
                    per.append(_score_wholerun(rollout, decided) if objective == "wholerun"
                               else (_score_horizon(rollout, decided, rows, est, horizon_s, state), 0.0))
                if len(per) == len(continuations):
                    scores[rank] = per
            teacher_rank = next((r for r in scores if "behaviour" in candidates[r].sources), None)
            own = candidates[teacher_rank] if teacher_rank is not None else None
            verdict["teacher_score"] = list(_mean_score(scores[teacher_rank])) if own else None
            verdict["teacher_pair"] = f"{own.robot_id}:{own.order_id}" if own else None
            take, best_rank = robust_choice(scores, teacher_rank, objective)
            if robust:
                verdict["teacher_scores"] = [list(s) for s in scores[teacher_rank]] if own else None
                if best_rank is not None:
                    verdict["best_pair"] = f"{candidates[best_rank].robot_id}:{candidates[best_rank].order_id}"
                    verdict["best_scores"] = [list(s) for s in scores[best_rank]]
            if take is not None:
                pick, best = candidates[take], _mean_score(scores[take])
                verdict.update(verdict="override", pair=f"{pick.robot_id}:{pick.order_id}",
                               pair_sources="+".join(pick.sources),
                               best_score=list(best), gain_s=float(verdict["teacher_score"][0] - best[0]))
                executor.allocator = ForcedPair(executor.allocator, executor, decision_id,
                                                pick.robot_id, pick.order_id)
            else:
                verdict["verdict"] = "keep"
        elif len(candidates) >= 2:
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
            own = candidates[int(teacher["candidate_rank"].iloc[0])] if len(teacher) else None
            verdict["teacher_pair"] = f"{own.robot_id}:{own.order_id}" if own else None
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


class _ScheduledPairs:
    """The wrapped strategy, with the oracle's override forced at each overridden decision."""

    def __init__(self, inner, executor, overrides: dict[int, tuple[str, str]]):
        self.inner = inner
        self.executor = executor
        self.overrides = overrides

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def assign(self, warehouse, robots, orders, fifo_position, cache=None, battery=None, timing=None):
        decision_id = self.executor.result.decisions - 1
        pair = self.overrides.get(decision_id)
        strategy = self.inner if pair is None else ForcedPair(self.inner, self.executor, decision_id, *pair)
        return strategy.assign(warehouse, robots, orders, fifo_position, cache, battery=battery, timing=timing)


def overrides_of(verdicts: list[dict]) -> dict[int, tuple[str, str]]:
    """decision_id -> (robot_id, order_id) the oracle forced."""
    return {v["decision_id"]: tuple(v["pair"].split(":", 1)) for v in verdicts if v.get("verdict") == "override"}


def replay_with_labels(build, verdicts: list[dict], seed: int):
    """The oracle's run once more, its overrides forced, recording features and the panel.

    (executor, feature rows, panel rows). The observers decide nothing, so the replay's
    tables equal the oracle's; replay_checked enforces that.
    """
    executor = build()
    panel = TeacherPanel(executor.allocator, executor, seed=seed)
    observer = FeatureObserver(panel, executor)
    executor.allocator = _ScheduledPairs(observer, executor, overrides_of(verdicts))
    executor.run()
    return executor, observer.rows, panel.rows


def replay_checked(build, oracle_executor, verdicts: list[dict], seed: int):
    """replay_with_labels, refused unless it reproduces the oracle's run exactly."""
    replay, features, panel = replay_with_labels(build, verdicts, seed)
    if tables(replay) != tables(oracle_executor):
        raise AlignmentError("the replay with features and panel differs from the oracle's run")
    return replay, features, panel


def oracle_run_id(source_run_id: str, objective: str, horizon_s: float,
                  continuations: tuple = ()) -> str:
    """The oracle run's own id: never the teacher run's, so both can sit in one corpus.

    A robust oracle (more than one continuation) gets its own id; the plain one keeps its old id.
    """
    key = f"{source_run_id}|oracle|{objective}|{horizon_s}|{DELTA_S}"
    if len(continuations) > 1:
        key += "|continuations=" + "+".join(str(name) for name in continuations)
    return sha256(key.encode()).hexdigest()[:16]


def write_trainable_run(out: Path, source_manifest: dict, replay, features: list, panel: list,
                        *, objective: str, horizon_s: float, verdicts: list[dict],
                        continuations: tuple = ()) -> dict:
    """The oracle's run as a recorded run folder; returns its manifest."""
    run_id = oracle_run_id(source_manifest["run_id"], objective, horizon_s, continuations)
    manifest = {
        **source_manifest,
        "run_id": run_id,
        "label_source": "oracle",
        "oracle": {
            "source_run_id": source_manifest["run_id"], "objective": objective,
            "horizon_s": horizon_s, "delta_s": DELTA_S,
            "continuations": [str(name) for name in continuations] or ["behaviour"],
            "overrides": sum(v.get("verdict") == "override" for v in verdicts),
            "kept": sum(v.get("verdict") == "keep" for v in verdicts),
        },
    }
    out.mkdir(parents=True, exist_ok=True)
    decisions, outcomes = (pd.DataFrame(rows).assign(run_id=run_id) for rows in tables(replay))
    decisions.to_parquet(out / "decisions.parquet")
    outcomes.to_parquet(out / "outcomes.parquet")
    pd.DataFrame(features).to_parquet(out / "features.parquet")
    pd.DataFrame(panel).to_parquet(out / PANEL_FILE)
    (out / "run.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path, help="recorded corpus run directory")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--max-decisions", type=int, default=None)
    parser.add_argument("--objective", choices=OBJECTIVES, default="jw2")
    parser.add_argument("--horizon", type=float, default=JW2.horizon_s, help="seconds, for --objective horizon")
    parser.add_argument("--continuations", nargs="+", default=None, metavar="NAME",
                        help="robust labels: continuation policies a pick must win under "
                             "(the behaviour's own name, or 'behaviour', is the behaviour policy)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    manifest = json.loads((args.run / "run.json").read_text(encoding="utf-8"))
    build = corpus_builder(manifest)
    behaviour = manifest["strategy"]
    label = f"{behaviour} r{manifest['robot_count']}"
    names = tuple(behaviour if n == "behaviour" else n for n in (args.continuations or ()))
    continuations = names or (None,)

    teacher = build()
    teacher.run()
    baseline = _metrics(teacher)
    print(f"[{label}] teacher replay: makespan {baseline['order_makespan_s']:.1f} s, "
          f"{baseline['decisions']} decisions", flush=True)

    tally = {"override": 0, "keep": 0}

    def progress(v, elapsed):
        tally[v["verdict"]] = tally.get(v["verdict"], 0) + 1
        if not args.quiet:
            extra = (f" -> {v['pair']} ({v.get('pair_sources', '')}) gains {v['gain_s']:.0f} s"
                     if v["verdict"] == "override" else "")
            print(f"[{label} {elapsed / 60:5.1f} min] decision {v['decision_id']:3d} t={v['t']:6.1f} s "
                  f"{v['verdict']}{extra}  (overrides {tally['override']}, kept {tally['keep']})",
                  flush=True)

    executor, verdicts = oracle_run(build, run_id=manifest["run_id"], behaviour=behaviour,
                                    max_decisions=args.max_decisions, progress=progress,
                                    objective=args.objective, horizon_s=args.horizon, manifest=manifest,
                                    continuations=continuations, seed=int(manifest["seed"]))
    oracle = _metrics(executor)
    print(f"[{label}] replaying the oracle's run with features and teacher panel", flush=True)
    replay, features, panel = replay_checked(build, executor, verdicts, int(manifest["seed"]))
    trained = write_trainable_run(args.out, manifest, replay, features, panel, objective=args.objective,
                                  horizon_s=args.horizon, verdicts=verdicts, continuations=names)
    report = {
        "oracle_run_id": trained["run_id"],
        "corpus_run": str(args.run), "run_id": manifest["run_id"], "behaviour": behaviour,
        "robots": manifest["robot_count"], "workload_seed": manifest.get("orders_path"),
        "objective": args.objective, "mode": "conservative", "horizon_s": args.horizon,
        "delta_s": DELTA_S, "continuations": list(names) or ["behaviour"],
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
