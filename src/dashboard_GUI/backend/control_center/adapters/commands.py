"""Read-only planning for the unchanged CLI runners."""
import ast
from dataclasses import asdict, replace
import hashlib
import importlib.util
import json
from pathlib import Path
import os

from fastapi import HTTPException
from control_center.adapters.catalogue import REPO, strategies
from control_center.adapters.scenarios import ScenarioError, load_scenario, scenario
from control_center.schemas.jobs import RunSpec
from control_center.storage.files import open_source, digest, read_json


def fail(message, code=409):
    raise HTTPException(code, message)


def hash_tree(path, limit=256 * 1024 * 1024):
    """Fingerprint all files; refuse links and oversized attempt archives."""
    if path.is_symlink():
        fail('Output path must not be a symlink')
    if not path.exists():
        return None
    if not path.is_dir():
        fail('Output path is not a directory')
    data, size = {}, 0
    for folder, dirs, files in os.walk(path, followlinks=False):
        for name in dirs:
            if (Path(folder) / name).is_symlink():
                fail('Output contains a symlink')
        for name in sorted(files):
            rel = str((Path(folder) / name).relative_to(path))
            with open_source(path, rel) as (handle, sig):
                size += sig[2]
                if size > limit:
                    fail('Existing output exceeds 256 MiB safe retry archive bound; use a new name')
                data[rel] = digest(handle)
    return data


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class CommandPlanner:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).absolute()
        self.output_root = self.data_dir / 'runs'
        self.python = REPO / '.pixi/envs/default/bin/python'

    def inputs(self, config, *, observed=False):
        files = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                 for package in ('mrta_core', 'simulation_env', 'task_management')
                 for p in sorted((REPO / 'src' / package).rglob('*.py'))}
        for p in (config.config_path, config.map_path, config.orders_path, REPO / 'pixi.lock', REPO / 'pixi.toml'):
            if p.is_file():
                files[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
        if not self.python.is_file():
            fail('Scientific interpreter is unavailable', 503)
        # Environment binary/lock fingerprint, without resolving away virtualenv semantics.
        files['interpreter'] = [str(self.python), str(self.python.resolve()),
                                hashlib.sha256(self.python.read_bytes()).hexdigest()]
        from simulation_env.dataset import git_state
        files['git'] = git_state(REPO)
        if observed:
            for relative in ('adapters/observed_runner.py', 'services/observation.py'):
                path = Path(__file__).parents[1] / relative
                files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        return files

    def build(self, spec, job_id):
        from simulation_env.fleet import home_bank
        from mrta_core.orders import load_orders_csv, select_orders
        scenario_id = spec.scenario_id if isinstance(spec, RunSpec) else 'W100'
        try:
            scenario_meta = scenario(scenario_id)
            config, warehouse = load_scenario(scenario_id)
        except ScenarioError as exc:
            fail(str(exc), 422)
        if isinstance(spec, RunSpec) and spec.input_id:
            from control_center.adapters.inputs import InputStore
            config = replace(config, orders_path=InputStore(self.data_dir).resolve(spec.input_id))
        capacity = len(home_bank(warehouse))
        known = {s['id'] for s in strategies()}
        chosen = [spec.strategy] if isinstance(spec, RunSpec) else spec.strategies
        if set(chosen) - known:
            fail('Unknown registered strategy', 422)
        counts = [spec.robots or config.robot_count] if isinstance(spec, RunSpec) else spec.robots
        if any(n > capacity for n in counts):
            fail(f'Fleet exceeds map capacity {capacity}', 422)
        observed = isinstance(spec, RunSpec) and spec.execution_mode == 'observed'
        if observed:
            from control_center.adapters.observed_runner import source_matches
            if not source_matches():
                fail('Original runner changed since observation parity; use CLI mode', 503)
        sources = self.inputs(config, observed=observed)
        common = dict(scenario_id=scenario_id, scenario_identity=scenario_meta['identity'],
                      scenario_hashes=scenario_meta['hashes'], config=asdict(config), capacity=capacity)
        warnings = ['Dirty source is recorded by the original runner; files can still change during execution.',
                    'No live motion telemetry; simulated time is available only where original logs/artifacts record it.']
        grid = []
        if isinstance(spec, RunSpec):
            output = self.output_root / 'singles' / job_id
            if output.exists() or output.is_symlink():
                fail('Single-run destination already exists')
            orders = load_orders_csv(config.orders_path, warehouse)
            effective = common | dict(robots=counts[0], orders=len(select_orders(orders, spec.orders)),
                seed=config.seed if spec.seed is None else spec.seed,
                window=config.allocation_window if spec.window is None else spec.window,
                quantum_s=config.dispatch_quantum_s if spec.quantum_s is None else spec.quantum_s,
                speed_mps=config.speed_mps if spec.speed_mps is None else spec.speed_mps)
            script = REPO / 'src/simulation_env/scripts/run_workload.py'
            argv = [str(self.python), str(script), '--config', str(config.config_path), '--strategy', spec.strategy,
                    '--robots', str(counts[0]), '--orders', str(spec.orders), '--seed', str(effective['seed']),
                    '--window', str(effective['window']), '--quantum', str(effective['quantum_s']),
                    '--speed', str(effective['speed_mps']), '--progress', str(spec.progress_interval_s),
                    '--dataset', str(output)]
            if spec.input_id:
                argv.extend(['--orders-file', str(config.orders_path)])
            if spec.quiet:
                argv.append('--quiet')
            before = None
        else:
            script = REPO / 'src/simulation_env/scripts/run_experiment.py'
            module_spec = importlib.util.spec_from_file_location('control_center_experiment_runner', script)
            # Runner imports generate_orders by its script-local module name.
            import sys
            folder = str(script.parent)
            if folder not in sys.path:
                sys.path.insert(0, folder)
            module = importlib.util.module_from_spec(module_spec)
            module_spec.loader.exec_module(module)
            grid = [list(row) for row in module.runs_for(spec.strategies, spec.robots, spec.seeds,
                                                       spec.workloads == 'seeded')]
            if len(grid) > 5000:
                fail('Experiment exceeds 5000-run planning bound', 422)
            output = self.output_root / 'experiments' / spec.name
            before = hash_tree(output)
            runner_spec = dict(strategies=spec.strategies, robots=spec.robots, seeds=spec.seeds,
                orders=spec.orders, workloads=spec.workloads,
                workload_orders=spec.workload_orders if spec.workloads == 'seeded' else None)
            if spec.mode == 'new' and before:
                fail('Experiment exists; explicitly preview resume or force')
            if spec.mode != 'new':
                owner = self.data_dir / 'experiment_owners' / f'{spec.name}.json'
                if not owner.is_file() or owner.is_symlink():
                    fail('Resume/force is restricted to previously app-owned experiments')
                if not output.is_dir():
                    fail('No existing experiment to resume or replace')
                if spec.mode == 'resume':
                    with open_source(owner.parent, owner.name) as (h, _):
                        ownership = read_json(h)
                    generation = output.stat()
                    if (ownership.get('output_identity') is not None
                            and ownership['output_identity'] != [generation.st_dev, generation.st_ino]):
                        fail('Output generation differs from recorded ownership; use a new name or explicit force')
                    if ownership.get('inputs_hash') != fingerprint(sources):
                        fail('Inputs/source changed since original experiment; use a new name')
                    with open_source(output, 'experiment.json') as (h, _):
                        record = read_json(h)
                    if record.get('spec') != runner_spec:
                        fail('Resume grid/settings differ from original experiment')
                    # Check actual files too, before runner can regenerate them.
                    if spec.workloads == 'seeded':
                        with open_source(output, 'workloads/workloads.json') as (h, _):
                            workload_index = read_json(h)
                        for seed in spec.seeds:
                            item = workload_index.get(str(seed), {})
                            with open_source(output, f'workloads/orders_s{seed}.csv') as (h, _):
                                if digest(h) != item.get('sha256'):
                                    fail('Existing workload hash changed; resume refused')
                    from control_center.adapters.validation import validate_resume
                    validate_resume(output, grid, spec, config, warehouse)
                warnings.append('Original runner may rewrite retry logs/workload index; previous output is archived in this job directory.')
                if spec.mode == 'force':
                    warnings.append('FORCE: original runner will rename the old experiment with a UTC suffix.')
            available_orders = spec.workload_orders if spec.workloads == 'seeded' else len(load_orders_csv(config.orders_path, warehouse))
            orders_per_run = min(spec.orders or available_orders, available_orders)
            if spec.orders > available_orders:
                warnings.append(f'Requested {spec.orders} orders per run, but the workload contains only {available_orders}; every run executes {orders_per_run} logical orders.')
            effective = common | dict(spec=runner_spec, jobs=spec.jobs, expected_runs=len(grid),
                                     quantum_s=config.dispatch_quantum_s, orders_per_run=orders_per_run)
            argv = [str(self.python), str(script), '--name', spec.name, '--out', str(output.parent),
                    '--strategies', *spec.strategies, '--robots', *map(str, spec.robots),
                    '--seeds', *map(str, spec.seeds), '--orders', str(spec.orders), '--jobs', str(spec.jobs),
                    '--workloads', spec.workloads, '--workload-orders', str(spec.workload_orders)]
            if spec.mode != 'new':
                argv.append('--' + spec.mode)
        if any(p.is_symlink() for p in (output, *output.parents)):
            fail('Output path contains a symlink')
        if spec.export:
            argv.append('--export')
        # Assert only source-declared CLI options, so source drift cannot create fake support.
        declared = {n.args[0].value for n in ast.walk(ast.parse(script.read_text()))
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == 'add_argument' and n.args and isinstance(n.args[0], ast.Constant)}
        if any(arg not in declared for arg in argv[2:] if arg.startswith('--')):
            fail('CLI capabilities changed; update reviewed command adapter', 503)
        payload = dict(job_id=job_id, kind=spec.kind, request=spec.model_dump(), argv=argv, cwd=str(REPO),
            output_dir=str(output), inputs_hash=fingerprint(sources), output_before=before,
            effective=effective, grid=grid, warnings=warnings)
        if observed:
            from control_center.adapters import observed_runner
            payload['execution_mode'] = 'observed'
            payload['original_cli_argv'] = list(argv)
            payload['argv'] = [argv[0], observed_runner.__file__, *argv[2:],
                '--telemetry-output', str(self.data_dir / 'jobs' / job_id / 'telemetry.ndjson'),
                '--job-id', job_id, '--source-fingerprint', payload['inputs_hash'],
                '--sample-interval', str(spec.observation_sample_s or 1)]
            payload['warnings'] = [w for w in warnings if not w.startswith('No live motion')]
            payload['warnings'].append('Explicit observed adapter: sampled cells/state; no continuous trajectory or matrix telemetry')
        if not isinstance(spec, RunSpec):
            st = output.stat() if output.is_dir() else None
            payload['output_identity_before'] = [st.st_dev, st.st_ino] if st else None
        payload = json.loads(json.dumps(payload, default=str))
        payload['fingerprint'] = fingerprint(payload)
        return payload

    def environment(self):
        # Whitelist: do not inherit PYTHONHOME/PYTHONSTARTUP or user module injection.
        env = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR') if k in os.environ}
        env.update(PYTHONPATH=os.pathsep.join(str(REPO / 'src' / p) for p in
                   ('mrta_core', 'simulation_env', 'task_management')),
                   PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1')
        return env
