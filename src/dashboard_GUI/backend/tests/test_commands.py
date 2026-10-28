from pathlib import Path

import pytest
from pydantic import ValidationError


def test_previews_are_read_only_and_cli_flags_are_source_true(tmp_path):
    from control_center.adapters.commands import CommandPlanner
    from control_center.schemas.jobs import RunSpec, ExperimentSpec
    planner = CommandPlanner(tmp_path / 'app')
    plan = planner.build(RunSpec(robots=2, orders=3, seed=7, quantum_s=3), 'job_a')
    args = plan['argv']
    assert args[1].endswith('/scripts/run_workload.py')
    assert args[args.index('--robots') + 1] == '2'
    assert args[args.index('--seed') + 1] == '7'
    assert args[args.index('--quantum') + 1] == '3.0'
    assert plan['effective']['orders'] == 3
    assert not (tmp_path / 'app').exists()
    p = planner.build(ExperimentSpec(name='tiny', strategies=['nearest_astar','random'], robots=[2],
                                    seeds=[1,2], workloads='reference', jobs=1), 'job_b')
    assert p['grid'] == [['nearest_astar', 2, 1], ['random', 2, 1], ['random', 2, 2]]
    assert '--quantum' not in p['argv'] and '--config' not in p['argv']
    assert p['argv'][p['argv'].index('--jobs') + 1] == '1'
    assert not (tmp_path / 'app').exists()


@pytest.mark.parametrize('values', [dict(robots=-1), dict(robots=True), dict(speed_mps=float('nan')),
    dict(quantum_s=-1), dict(window=0), dict(speed_mps='2'), dict(quiet='yes'), dict(executable='/bin/sh'), dict(scenario_id='../../etc')])
def test_run_validation_rejects_unsafe_or_impossible_input(values):
    from control_center.schemas.jobs import RunSpec
    with pytest.raises(ValidationError):
        RunSpec(**values)


@pytest.mark.parametrize('values', [dict(name='../x'), dict(name='x', seeds=[1,1]),
    dict(name='x', robots=[]), dict(name='x', quantum_s=3), dict(name='x', jobs=100),
    dict(name='x', strategies=['random','random'])])
def test_matrix_validation_bounds_grid_and_rejects_unsupported_flags(values):
    from control_center.schemas.jobs import ExperimentSpec
    with pytest.raises(ValidationError):
        ExperimentSpec(**values)


def test_source_fingerprint_and_destination_guard(tmp_path):
    from control_center.adapters.commands import CommandPlanner
    from control_center.schemas.jobs import RunSpec, ExperimentSpec
    from fastapi import HTTPException
    planner = CommandPlanner(tmp_path)
    with pytest.raises(HTTPException):
        planner.build(RunSpec(robots=101), 'a')
    with pytest.raises(HTTPException):
        planner.build(RunSpec(strategy='fake'), 'a')
    plan = planner.build(RunSpec(robots=1, orders=1), 'a')
    Path(plan['output_dir']).mkdir(parents=True)
    with pytest.raises(HTTPException):
        planner.build(RunSpec(robots=1, orders=1), 'a')
    with pytest.raises(HTTPException):
        planner.build(ExperimentSpec(name='absent', mode='resume'), 'b')


def test_reference_resume_does_not_require_seeded_workload_index(tmp_path):
    import json
    from control_center.adapters.commands import CommandPlanner
    from control_center.schemas.jobs import ExperimentSpec
    planner = CommandPlanner(tmp_path)
    spec = ExperimentSpec(name='reference', workloads='reference', robots=[1], seeds=[7])
    plan = planner.build(spec, 'a')
    folder = Path(plan['output_dir'])
    folder.mkdir(parents=True)
    (folder / 'experiment.json').write_text(json.dumps({'spec':plan['effective']['spec']}))
    owners = tmp_path / 'experiment_owners'
    owners.mkdir()
    (owners / 'reference.json').write_text(json.dumps({'inputs_hash':plan['inputs_hash']}))
    resumed = planner.build(spec.model_copy(update={'mode':'resume'}), 'b')
    assert '--resume' in resumed['argv']


def test_output_symlink_ancestor_is_refused_before_launch(tmp_path):
    from control_center.adapters.commands import CommandPlanner
    from control_center.schemas.jobs import RunSpec
    from fastapi import HTTPException
    target = tmp_path / 'outside'
    target.mkdir()
    root = tmp_path / 'app'
    root.mkdir()
    (root / 'runs').symlink_to(target, target_is_directory=True)
    with pytest.raises(HTTPException):
        CommandPlanner(root).build(RunSpec(robots=1, orders=1), 'a')
    assert list(target.iterdir()) == []


def test_interpreter_access_time_is_not_scientific_input_drift(tmp_path):
    import os
    from control_center.adapters.commands import CommandPlanner
    from control_center.adapters.catalogue import load_scenario
    planner = CommandPlanner(tmp_path / 'app')
    executable = tmp_path / 'python'
    executable.write_bytes(b'fixed interpreter bytes')
    planner.python = executable
    config, _ = load_scenario()
    before = planner.inputs(config)
    st = executable.stat()
    os.utime(executable, ns=(st.st_atime_ns + 1_000_000_000, st.st_mtime_ns))
    assert planner.inputs(config) == before


def test_matrix_preview_discloses_generated_order_clamp(tmp_path):
    from control_center.adapters.commands import CommandPlanner
    from control_center.schemas.jobs import ExperimentSpec
    plan=CommandPlanner(tmp_path).build(ExperimentSpec(name='clamp',robots=[20],seeds=[1,2],orders=50,workload_orders=3),'clamp_job')
    assert plan['effective']['orders_per_run']==3
    assert any('3' in w and '50' in w for w in plan['warnings'])
