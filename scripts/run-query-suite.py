"""Preview or execute the explicit query plan after strict ingestion preflight.

Default only writes a plan report. --execute runs pending profiles sequentially,
never restarts incomplete profiles, and revalidates provenance between children.
"""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import sys
from query_suite import campaign_lock, command, identifier, inspect_suite, load_tools, source_identity, validate_plan


def main():
    root = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--campaign', required=True, type=identifier)
    p.add_argument('--origin', default='sources', type=identifier)
    p.add_argument('--plan', required=True, type=Path)
    p.add_argument('--spec', required=True, type=Path)
    p.add_argument('--comparison-tools', type=Path, default=root / 'eval')
    p.add_argument('--port', type=int, default=8117)
    p.add_argument('--concurrency', type=int, default=1)
    p.add_argument('--locomo-data')
    p.add_argument('--memops-data')
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--execute', action='store_true')
    a = p.parse_args()
    if not 1 <= a.port <= 65535 or a.concurrency < 1:
        p.error('Invalid port or concurrency')
    plan, spec = json.loads(a.plan.read_text()), json.loads(a.spec.read_text())
    validate_plan(plan, spec, a.origin)
    tools = load_tools(a.comparison_tools)
    overrides = {'locomo': a.locomo_data, 'memops': a.memops_data}
    datasets = {b: (Path(v) if Path(v).is_absolute() else root / 'eval' / v) if v else root / f'eval/.data/{b}-dev.json' for b, v in overrides.items()}
    a.output.mkdir(parents=True, exist_ok=False)
    metadata = {'protocol': 'finite-query-suite-v1', 'campaign': a.campaign, 'execute': a.execute,
                'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'plan_sha256': tools.sha(a.plan), 'spec_sha256': tools.sha(a.spec),
                'controller_sha256': tools.sha(Path(__file__)),
                'suite_module_sha256': tools.sha(root / 'scripts/query_suite.py'),
                'comparison_module_sha256': tools.sha(a.comparison_tools / 'python/quality_comparison.py'),
                'runner_sha256': tools.sha(root / 'scripts/run-experiment.py'),
                'scope': 'Execution requires complete validated shared ingestion. Child exit zero is not quality success. Errors remain in planned question denominators. Independent human calibration and final acceptance are separate.'}
    launched = []
    with campaign_lock(root, a.campaign, a.execute):
        while True:
            state = inspect_suite(root, a.campaign, a.origin, plan, spec, a.spec, datasets, tools, source_identity(root))
            origins = [root / 'eval/artifacts' / f'{a.campaign}-{a.origin}-{b}/manifest.json' for b in plan['benchmarks']]
            traces = [json.loads(x.read_text()).get('service_configuration', {}).get('private_model_trace_enabled', False) for x in origins if x.exists()]
            trace = bool(traces and all(traces))
            if len(set(traces)) > 1:
                state['ready'] = False
                state['blockers'].append({'reason': 'Origin trace settings differ across benchmarks'})
            for job in state['jobs']:
                job['command'] = command(root, a.campaign, a.origin, job['profile'], a.spec.resolve(), a.port, a.concurrency, overrides, trace)
            report = {**metadata, **state, 'launched': launched,
                      'complete': state['ready'] and all(j['status'] == 'complete' for j in state['jobs'])}
            (a.output / 'suite.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
            if not a.execute:
                print(json.dumps({'ready': state['ready'], 'jobs': len(state['jobs']), 'blockers': state['blockers']}))
                return
            if not state['ready']:
                raise SystemExit('Query suite refused: inspect suite.json; no blocked profile was launched')
            if report['complete']:
                print(json.dumps({'event': 'query_suite_finished', 'campaign': a.campaign}))
                return
            # Reject changes to the plan or execution tools while this controller runs.
            files = [(a.plan, 'plan_sha256'), (a.spec, 'spec_sha256'),
                     (root / 'scripts/run-experiment.py', 'runner_sha256'),
                     (a.comparison_tools / 'python/quality_comparison.py', 'comparison_module_sha256')]
            if any(tools.sha(path) != metadata[key] for path, key in files):
                raise SystemExit('Query execution inputs changed; inspect before continuing')
            job = next(j for j in state['jobs'] if j['status'] == 'ready')
            print(json.dumps({'event': 'query_profile_started', 'profile': job['profile']}), flush=True)
            log_path = a.output / (job['profile'] + '.log')
            with log_path.open('x') as log:
                child = subprocess.run(job['command'], cwd=root, stdout=log, stderr=subprocess.STDOUT)
            launched.append({'profile': job['profile'], 'exit_code': child.returncode, 'log': str(log_path)})
            if child.returncode:
                report['launched'] = launched; report['ready'] = False
                report['blockers'].append({'profile': job['profile'], 'reason': 'Child runner failed'})
                (a.output / 'suite.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
                raise SystemExit('Child query runner failed; no automatic restart')


if __name__ == '__main__':
    main()
