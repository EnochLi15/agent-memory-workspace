"""Run one frozen query-only focus sample through the candidate classifier."""
import argparse, hashlib, json, os, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from experiment_runtime import ManagedRun, launch_detached


def read(path):
    return json.loads(Path(path).read_text())


def validate(plan_path):
    plan = read(plan_path)
    if plan['protocol'] != 'query-focus-validation-v1':
        raise ValueError('Unexpected validation protocol')
    required = [str(HERE), str(ROOT / 'scripts/experiment_runtime.py'), plan['client'], plan['config'],
                plan['original_config'], plan['queries'], plan['candidate_manifest'], plan['env_file']]
    if plan['client'] != str(HERE.with_name('query-focus-validation-client.mjs')) or not all(p in plan['pins'] for p in required):
        raise ValueError('Every actual execution and input file must be pinned')
    for name, expected in plan['pins'].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected:
            raise ValueError('Frozen input or execution file changed: ' + name)
    manifest = read(plan['candidate_manifest'])
    dist = Path(plan['candidate_dist'])
    actual = {str(p.relative_to(dist)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in dist.rglob('*') if p.is_file()}
    if actual != manifest['dist'] or manifest['commit'] != plan['candidate_commit']:
        raise ValueError('Candidate artifact changed')
    config = read(plan['config'])
    expected = {**read(plan['original_config']), 'queryFocus': True, 'queryFocusTimeout': 8000}
    if config != expected or config.get('mode') != 'enhanced' or config.get('retrieval') != 'hybrid' or config.get('experimental', {}).get('rawOnly'):
        raise ValueError('Only the reviewed enabled focus configuration is allowed')
    rows = read(plan['queries'])
    if len(rows) != 34 or len({r['qid'] for r in rows}) != 34 or len({r['query_sha256'] for r in rows}) != 34:
        raise ValueError('Exactly 34 unique frozen queries are required')
    for row in rows:
        if set(row) != {'qid', 'query', 'query_sha256'} or hashlib.sha256(row['query'].encode()).hexdigest() != row['query_sha256']:
            raise ValueError('Query-only input changed')
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--detach', action='store_true')
    actions.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    plan_path = args.plan.resolve()
    plan = validate(plan_path)
    campaign = plan_path.parent
    for name in ['runtime.json', 'results.private.jsonl', 'focus-results.private.json']:
        if (campaign / name).exists():
            raise FileExistsError('Preserve the consumed run: ' + name)
    if args.validate_only:
        print(json.dumps({'validation': 'pass', 'queries': 34, 'new_model_calls': 0}))
        return
    if args.detach:
        print(json.dumps(launch_detached([sys.executable, str(HERE), str(plan_path)], campaign / 'runtime.json', cwd=ROOT)))
        return
    env = os.environ.copy()
    for line in Path(plan['env_file']).read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    env.update(MEMORY_MODEL_AUDIT=str(campaign / 'model-calls.jsonl'), MEMORY_MODEL_TRACE=str(campaign / 'model-trace.private.jsonl'))
    with ManagedRun(campaign / 'runtime.json') as runtime:
        with (campaign / 'client.log').open('x') as log:
            child = runtime.spawn('focus-client', ['node', plan['client'], str(plan_path)], cwd=ROOT, env=env,
                                  stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            while child.poll() is None:
                runtime.poll()
                time.sleep(.25)
            runtime.poll()
            if child.returncode:
                raise RuntimeError('Query focus client failed; preserve every recorded outcome')
        validate(plan_path)
        results = read(campaign / 'focus-results.private.json')
        if len(results) != 34:
            raise ValueError('Incomplete focus results')


if __name__ == '__main__':
    main()
