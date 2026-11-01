"""Only fields the original runners support; no browser-supplied paths."""
from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Count = Annotated[int, Field(strict=True, ge=0, le=1_000_000)]
RobotCount = Annotated[int, Field(strict=True, ge=1, le=1000)]
Seed = Annotated[int, Field(strict=True, ge=0, le=2**32-1)]
Window = Union[Literal['fleet_size', 'legacy'], Annotated[int, Field(strict=True, ge=1, le=100000)]]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True)


class RunSpec(StrictModel):
    kind: Literal['run'] = 'run'
    scenario_id: str = Field('W100', pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    strategy: str = Field('nearest_astar', min_length=1, max_length=100)
    robots: Count = 0
    orders: Count = 0
    seed: Seed | None = None
    window: Window | None = None
    quantum_s: float | None = Field(None, ge=0, le=3600)
    speed_mps: float | None = Field(None, gt=0, le=100)
    progress_interval_s: float = Field(10.0, ge=0, le=3600)
    quiet: bool = True
    export: bool = False
    input_id: str | None = Field(None, pattern=r'^[a-f0-9]{64}$')
    execution_mode: Literal['cli', 'observed'] = 'cli'
    observation_sample_s: float | None = Field(None, ge=.001, le=3600)

    @model_validator(mode='after')
    def observation_mode(self):
        if self.execution_mode == 'cli' and self.observation_sample_s is not None:
            raise ValueError('observation_sample_s requires observed execution mode')
        return self


class ExperimentSpec(StrictModel):
    kind: Literal['experiment'] = 'experiment'
    name: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    strategies: list[str] = Field(default_factory=lambda: ['nearest_astar'], min_length=1, max_length=32)
    robots: list[RobotCount] = Field(default_factory=lambda: [10], min_length=1, max_length=100)
    seeds: list[Seed] = Field(default_factory=lambda: [1], min_length=1, max_length=100)
    orders: Count = 0
    jobs: int = Field(1, strict=True, ge=1, le=4)
    workloads: Literal['seeded', 'reference'] = 'seeded'
    workload_orders: Annotated[int, Field(strict=True, ge=1, le=1_000_000)] = 2000
    export: bool = False
    mode: Literal['new', 'resume', 'force'] = 'new'

    @field_validator('strategies', 'robots', 'seeds')
    @classmethod
    def unique(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('Duplicate values are not allowed')
        return value


Spec = Annotated[Union[RunSpec, ExperimentSpec], Field(discriminator='kind')]


class LaunchRequest(StrictModel):
    preview_id: str = Field(pattern=r'^[a-f0-9]{32}$')
