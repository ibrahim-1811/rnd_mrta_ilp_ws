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
    backup       116-120   a fresh validation pool, reserved 2026-10-31;
                           refused unless a run declares --purpose backup
    final        201-230   the sealed final evaluation set (adopted by
                           the researcher 2026-10-27); refused unless an
                           experiment declares --purpose final

Training is further split (DECISIONS.md, "Seed Partition Freeze"): rules
are induced on 104-110; 101-103 stay exploratory after M1 inspected them.

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
BACKUP_RESERVED = range(116, 121)       # Fresh validation pool, researcher approval only
FINAL_RESERVED = range(201, 231)

ROLES = {
    "training": TRAINING,
    "validation": VALIDATION,
    "evaluation": EVALUATION,
    "backup": BACKUP_RESERVED,
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
    and evaluation seeds (1-30, 201-230). Refuses an empty list too: a
    fit that names no seed has not shown where its data came from.
    """
    seeds = list(seeds)
    if not seeds:
        raise SeedLeakageError("no seeds declared; induction data must name its seeds")
    check_purpose(seeds, purpose="induction")


def check_run_is_induction(manifest) -> None:
    """Enforce the induction pool on a recorded run, from its manifest.

    Fails closed: the workload must identify its seed, since a safe
    strategy seed says nothing about the workload it executed.
    """

    workload = workload_seed_of(manifest.get("orders_path", ""))
    if workload is None:
        raise SeedLeakageError(
            f"workload {manifest.get('orders_path')!r} does not identify its seed"
        )

    strategy = manifest.get("seed")
    if strategy is None:
        raise SeedLeakageError("run manifest does not record its strategy seed")

    check_induction_purpose([workload, strategy])


def role_of(seed: int) -> str | None:
    """The role a seed belongs to, or None for a development seed."""

    for role, seeds in ROLES.items():
        if seed in seeds:
            return role

    return None


def check_purpose(seeds, purpose: str | None) -> None:
    """Refuse seeds that do not belong to the experiment's purpose.

    ``purpose`` None is development work: any seed except the reserved
    final and backup sets. Otherwise every seed must belong to that role or sub-role.
    """

    if purpose is None:
        sealed = sorted(seed for seed in seeds if seed in FINAL_RESERVED)

        if sealed:
            raise SeedLeakageError(
                f"seeds {sealed} are reserved for the final evaluation; "
                "declare --purpose final to use them"
            )

        backup = sorted(seed for seed in seeds if seed in BACKUP_RESERVED)

        if backup:
            raise SeedLeakageError(
                f"seeds {backup} are the reserved backup validation pool; "
                "declare --purpose backup to use them"
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


SEALED = ("validation", "backup", "evaluation", "final")

_WORKLOAD_FILE = re.compile(r"orders_s(\d+)\.csv$")


def workload_seed_of(orders_path) -> int | None:
    """The seed a workload file was generated from, by its name.

    run_experiment.py writes seed s's workload as orders_s<s>.csv; that
    name is the provenance it records. Any other file has no known seed.
    """

    match = _WORKLOAD_FILE.search(str(orders_path))
    return int(match.group(1)) if match else None


def sealed_inputs(*, strategy_seed, orders_path, require_known_workload=False) -> list[str]:
    """What, if anything, makes a run off limits to diagnostics.

    Both seeds a run has are checked, separately: the strategy's random
    stream, and the workload it executes. A safe strategy seed says
    nothing about the workload. The seed of a workload is known only
    from its file name, so ``require_known_workload`` makes any other
    name a refusal: a renamed sealed workload must not read as clean.
    """

    found = []
    workload = workload_seed_of(orders_path)

    if workload is None and require_known_workload:
        found.append(f"workload {orders_path} of unknown seed")

    if workload is not None and role_of(workload) in SEALED:
        found.append(f"workload seed {workload} ({role_of(workload)})")

    if strategy_seed is not None and role_of(strategy_seed) in SEALED:
        found.append(f"strategy seed {strategy_seed} ({role_of(strategy_seed)})")

    return found
