"""Explicit observed-mode planning and tiny original-CLI scientific parity."""
import json
from pathlib import Path
import subprocess

import pytest

from control_center.adapters.catalogue import REPO
from control_center.adapters.commands import CommandPlanner
from control_center.schemas.jobs import RunSpec


def test_observed_mode_is_explicit_and_original_cli_stays_default(tmp_path):
    planner = CommandPlanner(tmp_path / 'app')
    normal = planner.build(RunSpec(robots=2, orders=3), 'plain')
    assert normal['argv'][1].endswith('/scripts/run_workload.py')
    observed = planner.build(RunSpec(robots=2, orders=3, execution_mode='observed'), 'observed')
    assert observed['execution_mode'] == 'observed'
    assert observed['argv'][1].endswith('/adapters/observed_runner.py')
    assert observed['original_cli_argv'][1].endswith('/scripts/run_workload.py')
    assert observed['argv'][observed['argv'].index('--telemetry-output') + 1] == str(tmp_path / 'app/jobs/observed/telemetry.ndjson')
    assert not (tmp_path / 'app').exists()


def test_matrix_observation_and_invalid_intervals_are_refused():
    from pydantic import ValidationError
    from control_center.schemas.jobs import ExperimentSpec
    for params in ({'execution_mode': 'observed', 'observation_sample_s': 0},
                   {'execution_mode': 'observed', 'observation_sample_s': float('nan')},
                   {'execution_mode': 'cli', 'observation_sample_s': 1}):
        with pytest.raises(ValidationError):
            RunSpec(**params)
    with pytest.raises(ValidationError):
        ExperimentSpec(name='matrix', execution_mode='observed')


def test_runner_drift_disables_observed_mode_without_disabling_cli(tmp_path, monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr('control_center.adapters.observed_runner.RUNNER_SHA256', 'wrong')
    planner = CommandPlanner(tmp_path / 'app')
    with pytest.raises(HTTPException) as exc:
        planner.build(RunSpec(execution_mode='observed', orders=1, robots=1), 'observed')
    assert exc.value.status_code == 503
    assert planner.build(RunSpec(orders=1, robots=1), 'plain')['argv'][1].endswith('run_workload.py')


@pytest.mark.parametrize('strategy,window,quantum', [('nearest_astar', 'fleet_size', 3), ('random', 'legacy', 0)])
def test_original_cli_and_observed_adapter_have_identical_scientific_artifacts(tmp_path, strategy, window, quantum):
    from control_center.adapters.observed_runner import __file__ as adapter
    from simulation_env.dataset_export import read_run
    planner = CommandPlanner(tmp_path / 'app')
    common = ['--config', str(REPO / 'src/simulation_env/config/warehouse.yaml'), '--robots', '2',
        '--orders', '3', '--seed', '7', '--strategy', strategy, '--window', window, '--quantum', str(quantum),
        '--progress', '0', '--quiet', '--export']
    outputs = [tmp_path / 'cli', tmp_path / 'observed']
    for script, output in zip((REPO / 'src/simulation_env/scripts/run_workload.py', Path(adapter)), outputs):
        argv = [str(planner.python), str(script), *common, '--dataset', str(output)]
        if output.name == 'observed':
            argv += ['--telemetry-output', str(tmp_path / 'telemetry.ndjson'), '--job-id', 'parity',
                     '--source-fingerprint', 'test-inputs', '--sample-interval', '1']
        done = subprocess.run(argv, cwd=REPO, env=planner.environment(), capture_output=True, timeout=20)
        assert done.returncode == 0, done.stdout.decode()[-1000:] + done.stderr.decode()[-1000:]
    assert read_run(outputs[0]) == read_run(outputs[1])
    host = {'wall_clock_s', 'allocator_cpu_s', 'mission_build_cpu_s', 'worst_dispatch_s'}
    metrics = [json.loads((p / 'metrics.json').read_text()) for p in outputs]
    assert {k:v for k,v in metrics[0].items() if k not in host} == {k:v for k,v in metrics[1].items() if k not in host}
    for name in ('decisions.csv', 'outcomes.csv', 'bk.pl', 'exs.pl'):
        assert (outputs[0] / name).read_bytes() == (outputs[1] / name).read_bytes()
    records = [json.loads(line) for line in (tmp_path / 'telemetry.ndjson').read_text().splitlines()]
    assert records[-1]['payload']['complete'] is True
    snapshots = [r for r in records if r['type'] == 'fleet_snapshot']
    assert snapshots[0]['sim_time_exact'] == '0'
    assert snapshots[-1]['payload']['orders']['completed'] == 3
