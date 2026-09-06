"""Finite query-only execution over a proven complete shared ingestion."""
import copy
from contextlib import contextmanager
import fcntl
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

QUERY_ENV = {
    'MEMORY_RERANK': ('rerank', bool),
    'MEMORY_COVERAGE_PACKING': ('coveragePacking', bool),
    'MEMORY_EVENT_VIEW': ('eventView', bool),
    'MEMORY_EXPERIMENT_MULTI_HOP': ('experimental.multiHop', bool),
    'MEMORY_RELATION_MODE': ('relationMode', str),
    'MEMORY_RERANK_POLICY': ('rerankPolicy', str),
    'MEMORY_RERANK_FORMAT': ('rerankFormat', str),
    'MEMORY_CANDIDATE_LIMIT': ('candidateLimit', int),
    'MEMORY_RERANK_CANDIDATES': ('rerankCandidates', int),
    'MEMORY_RAW_FALLBACK': ('rawFallback', bool),
    'MEMORY_RETRIEVAL': ('retrieval', str),
    'MEMORY_SEARCH_TIMEOUT_MS': ('searchTimeout', int),
}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', value) or value in ('.', '..'):
        raise ValueError('Invalid experiment identifier')
    return value


def validate_plan(plan, spec, origin):
    if plan.get('protocol') != 'paired-query-quality-plan-v1':
        raise ValueError('Unsupported query plan')
    profiles = plan['profiles']
    if not profiles or len(profiles) != len(set(profiles)) or set(profiles) != set(spec['profiles']):
        raise ValueError('Plan and spec must contain the same unique profiles')
    if plan['benchmarks'] != ['locomo', 'memops'] or origin not in profiles:
        raise ValueError('This suite requires both benchmarks and a declared origin profile')
    for name in profiles:
        identifier(name)
        profile = spec['profiles'][name]
        if profile.get('baseline') or set(profile.get('environment', {})) - QUERY_ENV.keys():
            raise ValueError('Query profiles may only change supported retrieval settings')
    pairs = plan['pairs']
    if not pairs or any(p['baseline'] not in profiles or p['candidate'] not in profiles
                        or p['baseline'] == p['candidate']
                        or p['kind'] not in ('query_component', 'query_bundle', 'query_repeat') for p in pairs):
        raise ValueError('Invalid planned comparison')
    if len({(p['baseline'], p['candidate']) for p in pairs}) != len(pairs):
        raise ValueError('Duplicate planned comparison')


def expected_config(origin_config, spec, profile):
    result = copy.deepcopy(origin_config)
    env = {**spec['defaults'], **spec['profiles'][profile].get('environment', {})}
    for key, (field, kind) in QUERY_ENV.items():
        if key not in env:
            continue
        value = env[key]
        if kind is bool:
            if value not in ('true', 'false'):
                raise ValueError('Boolean query setting must be true or false: ' + key)
            value = value == 'true'
        elif kind is int:
            value = int(value)
            if value <= 0:
                raise ValueError('Query capacity must be positive: ' + key)
        elif not isinstance(value, str):
            raise ValueError('Query setting must be a string: ' + key)
        if '.' in field:
            parent, child = field.split('.')
            result[parent][child] = value
        else:
            result[field] = value
    return result


def comparable(config):
    return {k: v for k, v in config.items() if k not in ('host', 'port', 'ingestion_origin')}


def load_tools(directory):
    path = Path(directory) / 'python/quality_comparison.py'
    spec = importlib.util.spec_from_file_location('query_quality_tools', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_identity(root):
    return {part: {'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root / part, text=True).strip(),
                   'dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=root / part, text=True).strip())}
            for part in ('service', 'eval')}


def inspect_suite(root, campaign, origin, plan, spec, spec_path, datasets, tools, identities):
    artifacts = root / 'eval/artifacts'
    by_hash = {tools.sha(path): path for path in datasets.values()}
    origins, blockers = {}, []
    for benchmark in plan['benchmarks']:
        run_id = f'{campaign}-{origin}-{benchmark}'
        try:
            run = tools.load_run(artifacts / run_id, by_hash)
            if run['status'] != 'ready':
                raise ValueError('Origin is ' + run['status'])
            m = run['manifest']
            if {q['benchmark'] for q in run['questions'].values()} != {benchmark}:
                raise ValueError('Origin question benchmark differs from plan')
            if m['dataset_sha256'] != tools.sha(datasets[benchmark]):
                raise ValueError('Origin benchmark dataset differs')
            for part, identity in identities.items():
                if identity['dirty'] or identity['commit'] != m[part + '_commit']:
                    raise ValueError('Current source differs or is dirty: ' + part)
            if m['service_configuration'].get('experiment_spec_sha256') != tools.sha(spec_path):
                raise ValueError('Origin experiment spec differs')
            if any(m.get(k) != spec.get('evaluation', {}).get(k) for k in ('answer_model', 'judge_model')):
                raise ValueError('Origin Answer/Judge differs from the explicit spec')
            if comparable(m['service_configuration']) != comparable(expected_config(m['service_configuration'], spec, origin)):
                raise ValueError('Origin retrieval settings differ from spec')
            tools.origin_proof(run, benchmark, artifacts)
            origins[benchmark] = run
        except (ValueError, KeyError, FileNotFoundError) as error:
            blockers.append({'run_id': run_id, 'reason': str(error)})
    jobs = []
    for profile in plan['profiles']:
        if profile == origin:
            continue
        job = {'profile': profile, 'status': 'pending', 'benchmarks': []}
        for benchmark in plan['benchmarks']:
            run_id = f'{campaign}-{profile}-{benchmark}'
            try:
                run = tools.load_run(artifacts / run_id, by_hash)
                state = run['status']
                if state == 'ready' and benchmark in origins:
                    m = run['manifest']
                    if comparable(m['service_configuration']) != comparable(expected_config(origins[benchmark]['manifest']['service_configuration'], spec, profile)):
                        raise ValueError('Finished profile has different recorded settings')
                    comparison = tools.compare(origins[benchmark], run, 'query_bundle', artifacts)
                    if comparison['status'] != 'compared':
                        raise ValueError('Finished profile failed paired provenance: ' + str(comparison.get('reason')))
                if state not in ('ready', 'not_started'):
                    raise ValueError('Existing run is ' + state + '; inspect it before continuing')
                job['benchmarks'].append({'benchmark': benchmark, 'run_id': run_id, 'status': state})
            except (ValueError, KeyError, FileNotFoundError) as error:
                job['benchmarks'].append({'benchmark': benchmark, 'run_id': run_id, 'status': 'refused', 'reason': str(error)})
        states = [x['status'] for x in job['benchmarks']]
        if 'refused' in states or ('ready' in states and 'not_started' in states):
            job['status'] = 'refused'
            blockers.append({'profile': profile, 'reason': 'Existing profile is inconsistent or only partly complete'})
        elif all(x == 'ready' for x in states) and len(origins) == 2:
            job['status'] = 'complete'
        elif all(x == 'not_started' for x in states):
            # A service artifact without manifests can be an interrupted start.
            if (root / 'artifacts' / campaign / (profile + '-service.json')).exists():
                job['status'] = 'refused'
                blockers.append({'profile': profile, 'reason': 'Existing service configuration without complete runs'})
            else:
                job['status'] = 'ready' if len(origins) == 2 else 'pending'
        jobs.append(job)
    return {'ready': not blockers and len(origins) == 2, 'blockers': blockers, 'jobs': jobs}


def command(root, campaign, origin, profile, spec_path, port, concurrency, overrides, trace):
    args = [sys.executable, str(root / 'scripts/run-experiment.py'), '--campaign', campaign,
            '--profile', profile, '--reuse-ingestion', origin, '--spec', str(spec_path),
            '--port', str(port), '--concurrency', str(concurrency), '--benchmark', 'both']
    for benchmark, path in overrides.items():
        if path is not None:
            args += ['--' + benchmark + '-data', str(path)]
    if trace:
        args.append('--trace-models')
    return args


@contextmanager
def campaign_lock(root, campaign, execute):
    if not execute:
        yield
        return
    directory = root / 'artifacts' / identifier(campaign)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'query-suite.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Another query suite is executing this campaign') from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
