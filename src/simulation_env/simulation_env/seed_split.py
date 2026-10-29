"""Which workload seeds may be used for what.

A seed selects a workload (generate_orders.py, docs/DECISIONS.md,
"Workload Seeds and Paired Comparison"). A learner must never see a
workload it is later evaluated on, so every seed has at most one role
(docs/DECISIONS.md, "Seed Split"):

    training     101-110   ILP examples come from these runs only
    validation   111-115   learner settings (thresholds, bias, limits)
                           are chosen on these, never on evaluation
    evaluation   1-30      the frozen main study
                           (EXPERIMENT_PROTOCOL.md, "Seeds")
    final        201-230   the sealed final evaluation set (adopted by
                           the researcher 2026-10-27); refused unless an
                           experiment declares --purpose final

Any other seed -- 42, the reference workload, among them -- is a
development seed: fine for debugging, never for training, tuning or a
reported result.

The guard is deliberately simple: an experiment that declares a purpose
may only use that role's seeds, and an experiment that declares none
may not touch the reserved final set.
"""

import re


TRAINING = range(101, 111)
TRAINING_EXPLORATORY = range(101, 104)  # M1 diagnostic & exploratory development
TRAINING_INDUCTION = range(104, 111)    # Primary ILP rule induction pool (M3-M6)
VALIDATION = range(111, 116)
EVALUATION = range(1, 31)
FINAL_RESERVED = range(201, 231)

ROLES = {
    "training": TRAINING,
    "validation": VALIDATION,
    "evaluation": EVALUATION,
    "final": FINAL_RESERVED,
}

SUBROLES = {
    "induction": TRAINING_INDUCTION,
    "exploratory": TRAINING_EXPLORATORY,
}


class SeedLeakageError(ValueError):
    """A seed used outside the role it is reserved for."""


def is_induction_seed(seed: int) -> bool:
    """Whether a seed belongs to the primary ILP induction pool (104-110)."""
    return seed in TRAINING_INDUCTION


def check_induction_purpose(seeds) -> None:
    """Enforce that seeds belong strictly to the frozen induction pool (104-110).

    Refuses exploratory seeds (101-103), validation seeds (111-115),
    and evaluation seeds (1-30, 201-230).
    """
    check_purpose(seeds, purpose="induction")


def role_of(seed: int) -> str | None:
    """The role a seed belongs to, or None for a development seed."""

    for role, seeds in ROLES.items():
        if seed in seeds:
            return role

    return None


def check_purpose(seeds, purpose: str | None) -> None:
    """Refuse seeds that do not belong to the experiment's purpose.

    ``purpose`` None is development work: any seed except the reserved
    final set. Otherwise every seed must belong to that role or sub-role.
    """

    if purpose is None:
        sealed = sorted(seed for seed in seeds if seed in FINAL_RESERVED)

        if sealed:
            raise SeedLeakageError(
                f"seeds {sealed} are reserved for the final evaluation; "
                "declare --purpose final to use them"
            )

        return

    all_purposes = {**ROLES, **SUBROLES}
    if purpose not in all_purposes:
        raise SeedLeakageError(f"unknown purpose {purpose!r}; known: {sorted(all_purposes)}")

    allowed = all_purposes[purpose]
    outside = sorted(seed for seed in seeds if seed not in allowed)

    if outside:
        raise SeedLeakageError(
            f"seeds {outside} are not {purpose} seeds "
            f"({allowed.start}-{allowed.stop - 1})"
        )


SEALED = ("evaluation", "final")

_WORKLOAD_FILE = re.compile(r"orders_s(\d+)\.csv$")


def workload_seed_of(orders_path) -> int | None:
    """The seed a workload file was generated from, by its name.

    run_experiment.py writes seed s's workload as orders_s<s>.csv; that
    name is the provenance it records. Any other file has no known seed.
    """

    match = _WORKLOAD_FILE.search(str(orders_path))
    return int(match.group(1)) if match else None


def sealed_inputs(*, strategy_seed, orders_path) -> list[str]:
    """What, if anything, makes a run an evaluation or final run.

    Both seeds a run has are checked, separately: the strategy's random
    stream, and the workload it executes. A safe strategy seed says
    nothing about the workload.
    """

    found = []
    workload = workload_seed_of(orders_path)

    if workload is not None and role_of(workload) in SEALED:
        found.append(f"workload seed {workload} ({role_of(workload)})")

    if strategy_seed is not None and role_of(strategy_seed) in SEALED:
        found.append(f"strategy seed {strategy_seed} ({role_of(strategy_seed)})")

    return found
