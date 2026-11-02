"""M4-M6 labels: what each teacher would pick at the same recorded decision.

M4, M5 and M6 must learn from one set of decisions, differing in labels only
(M2 spec 6.1), or a difference in results could be a difference in data. So one
behaviour policy drives the run, and at each decision every teacher is asked
for its picks on the identical state. The answers are recorded and never acted
on, so a teacher here is a label source, not a run.

Each teacher gets its own copy of the battery context, because eligibility
counts accumulate on it and those are run metrics.
"""

from dataclasses import replace

from task_management.allocation_strategy import get_batch_strategy


PANEL_FILE = "teacher_labels.parquet"

# M2 spec 6.4: random and nearest_manhattan would dilute grid-aware teachers.
PANEL_TEACHERS = (
    "nearest_astar",
    "earliest_finish",
    "multi_criteria",
    "auction",
    "hungarian",
    "milp",
)


class TeacherPanel:
    """Wraps the acting strategy; records each teacher's picks; decides nothing."""

    def __init__(self, inner, executor=None, teachers=PANEL_TEACHERS, seed=0):
        self.inner = inner
        self.executor = executor
        self.teachers = {name: get_batch_strategy(name, seed=seed) for name in teachers}
        self.rows = []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def assign(self, warehouse, robots, orders, fifo_position, cache=None, battery=None, timing=None):
        decision_id = self.executor.result.decisions - 1

        for name, teacher in self.teachers.items():
            picks = teacher.assign(
                warehouse,
                robots,
                orders,
                fifo_position,
                cache,
                battery=None if battery is None else replace(battery),
                timing=timing,
            )
            self.rows.extend(
                {
                    "decision_id": decision_id,
                    "teacher": name,
                    "robot_id": pick.robot_id,
                    "order_id": pick.order_id,
                }
                for pick in picks
            )

        return self.inner.assign(
            warehouse, robots, orders, fifo_position, cache, battery=battery, timing=timing
        )
