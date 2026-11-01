"""Read production declarations; never construct an allocator or executor."""
from control_center.adapters.scenarios import REPO, geometry, load_scenario, scenarios

CONFIG = REPO / 'src/simulation_env/config/warehouse.yaml'


def strategies():
    from task_management.allocation_strategy import BATCH_STRATEGIES
    return [dict(id=name, implementation=cls.__name__, registered=True,
                 review_status='See docs/CURRENT_PHASE.md; registration is not scientific approval')
            for name, cls in sorted(BATCH_STRATEGIES.items())]
