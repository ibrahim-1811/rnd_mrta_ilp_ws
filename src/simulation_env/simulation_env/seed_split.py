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
    final        201-230   PROPOSED sealed set, reserved until the
                           researcher decides; refused unless an
                           experiment declares --purpose final

Any other seed -- 42, the reference workload, among them -- is a
development seed: fine for debugging, never for training, tuning or a
reported result.

The guard is deliberately simple: an experiment that declares a purpose
may only use that role's seeds, and an experiment that declares none
may not touch the reserved final set.
"""


TRAINING = range(101, 111)
VALIDATION = range(111, 116)
EVALUATION = range(1, 31)
FINAL_RESERVED = range(201, 231)

ROLES = {
    "training": TRAINING,
    "validation": VALIDATION,
    "evaluation": EVALUATION,
    "final": FINAL_RESERVED,
}


class SeedLeakageError(ValueError):
    """A seed used outside the role it is reserved for."""


def role_of(seed: int) -> str | None:
    """The role a seed belongs to, or None for a development seed."""

    for role, seeds in ROLES.items():
        if seed in seeds:
            return role

    return None


def check_purpose(seeds, purpose: str | None) -> None:
    """Refuse seeds that do not belong to the experiment's purpose.

    ``purpose`` None is development work: any seed except the reserved
    final set. Otherwise every seed must belong to that role.
    """

    if purpose is None:
        sealed = sorted(seed for seed in seeds if seed in FINAL_RESERVED)

        if sealed:
            raise SeedLeakageError(
                f"seeds {sealed} are reserved for the final evaluation; "
                "declare --purpose final to use them"
            )

        return

    if purpose not in ROLES:
        raise SeedLeakageError(f"unknown purpose {purpose!r}; known: {sorted(ROLES)}")

    outside = sorted(seed for seed in seeds if seed not in ROLES[purpose])

    if outside:
        raise SeedLeakageError(
            f"seeds {outside} are not {purpose} seeds "
            f"({ROLES[purpose].start}-{ROLES[purpose].stop - 1})"
        )
