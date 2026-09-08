"""Freeze and validate isolated, single-attempt HTTP add probes; never run QA.

--plan/--validate make no model calls. --run requires both predecessor controllers
and all their owned children to be stopped. Existing execution output is immutable.
"""
import argparse
from collections import Counter
from contextlib import ExitStack, closing
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('failed_tail_helpers', Path(__file__).with_name('run-failed-tail-evaluation.py'))
tail = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tail)
from experiment_runtime import ManagedRun, RunInterrupted, launch_detached, now, read_status, write_json
sha, read_json = tail.sha, tail.read_json
INPUTS = ROOT / 'artifacts/priority-failed-adds-20260909-01/inputs.private.json'
INPUT_SHA = '6587bc6cf011991d47d436fbf334fa1817ce971c37d9b913c1ed38a1e6ce3821'
SCOPE = 'One HTTP /add per frozen failed request, using mixed-version historical prefixes. No HTTP retry, QA evaluation, rejudging, or score changes.'


def all_hashes(directory):
    directory = Path(directory)
    if any(p.is_symlink() for p in directory.rglob('*')):
        raise ValueError('Compiled artifacts must not contain symlinks')
    return {str(p.relative_to(directory)): sha(p.read_bytes()) for p in sorted(directory.rglob('*')) if p.is_file()}


def candidate(dist, manifest, commit):
    if not dist or not manifest or not commit:
        raise ValueError('Candidate dist, manifest and expected commit are required')
    directory, _, provenance = tail.candidate_input(dist=dist, manifest=manifest, expected_commit=commit)
    declared = read_json(manifest).get('dist', read_json(manifest).get('dist_hashes'))
    actual = all_hashes(directory)
    if actual != declared:
        raise ValueError('Manifest must pin every compiled file, including declarations and maps')
    return directory, actual, provenance


def idle(inputs, require=False):
    original = tail.origin_state(Path(inputs['origin']))
    previous = Path(inputs['previous_recovery'])
    blockers = ['full:' + x for x in original['blockers']]
    if not (previous / 'runtime.json').exists():
        blockers.append('recovery:missing_ownership_record')
        status = 'unknown'
    else:
        state = read_status(previous / 'runtime.json')
        status = state['status']
        if state.get('alive') or any(c.get('alive') for c in state.get('children', {}).values()):
            blockers.append('recovery:controller_or_owned_child_alive')
        if status not in ('finished', 'failed', 'interrupted'):
            blockers.append('recovery:controller_not_terminal')
    if require and blockers:
        raise ValueError('Cloud execution blocked: ' + ', '.join(blockers))
    return {'cloud_start_allowed': not blockers, 'blockers': blockers,
            'full_status': original['origin_runtime_status'], 'recovery_status': status}


def wires(path):
    # JSON.stringify must match the frozen evaluator and service request hash.
    program = """import {readFileSync} from 'node:fs';import {createHash} from 'node:crypto';
const data=JSON.parse(readFileSync(process.argv[1],'utf8'));
console.log(JSON.stringify(data.cases.map(c=>{const body=JSON.stringify(c.request);
return {request_id:c.request_id,body,hash:createHash('sha256').update(body).digest('hex')};})));"""
    return json.loads(subprocess.check_output(['node', '--input-type=module', '-e', program, str(path)], text=True))


def validate_inputs(path, expected_hash):
    if sha(Path(path).read_bytes()) != expected_hash:
        raise ValueError('Frozen private input hash changed')
    data = read_json(path)
    origin = Path(data['origin'])
    original, _ = tail.pinned_origin(origin)
    if sha((origin / 'plan.json').read_bytes()) != data['origin_plan_sha256']:
        raise ValueError('Original campaign plan changed')
    if Path(data['original_service_config']) != origin / 'service.json' or sha(Path(data['original_service_config']).read_bytes()) != data['original_service_config_sha256']:
        raise ValueError('Original service configuration changed')
    if data['namespace'] != original['namespace'] or data['env_file'] != original['env_file']:
        raise ValueError('Original namespace or model environment path changed')
    phase = next(p for p in original['phases'] if p['id'] == 'memops-risk')
    schedule = tail.request_schedule(Path(original['code_root']) / 'eval', Path(phase['data_file']), data['namespace'])
    wire = wires(path)
    if not data['cases'] or len({c['request_id'] for c in data['cases']}) != len(data['cases']) or len({c['sample_id'] for c in data['cases']}) != len(data['cases']):
        raise ValueError('Each case must have its own unique request and tenant')
    receipts = {}
    for case, packet in zip(data['cases'], wire):
        req, revision = case['request'], case['revision']
        user = data['namespace'] + ':memops:' + case['sample_id']
        if req['request_id'] != case['request_id'] or req['user_id'] != user or not case['request_id'].startswith(user + ':'):
            raise ValueError('Request identity differs from original tenant')
        if packet['hash'] != case['request_sha256'] or case['prefix_receipts'] != revision:
            raise ValueError('Original request hash or prefix receipt count changed')
        if sha(Path(case['snapshot']).read_bytes()) != case['snapshot_sha256']:
            raise ValueError('Immutable snapshot hash changed')
        expected = [r for r in schedule if r['sample_id'] == case['sample_id']]
        if expected[revision]['hash'] != packet['hash']:
            raise ValueError('Original failed request differs from frozen evaluator schedule')
        records = tail.snapshot_receipts(case['snapshot'], schedule, data['namespace'], case['sample_id'], revision, case['request_id'][len(user)+1:])
        receipts.update(records)
    return data, original, wire, receipts


def fresh(campaign, allow_owned_launch=False, planning=False):
    tail.fresh_outputs(campaign, campaign / 'never-create-evaluation-output', allow_owned_launch)
    outputs = ['results.private.json', 'summary.json', 'model-trace.jsonl', 'model-calls.jsonl', 'http-results', 'service.json']
    if planning:
        outputs += ['plan.json', 'service-dist', 'candidate-manifest.json', 'frozen-inputs.private.json',
                    'wire-requests.private.json', 'prefix-receipts.private.json', 'service-launcher.mjs', 'http-client.mjs', 'node_modules', 'package.json']
    for name in outputs:
        if (campaign / name).exists():
            raise FileExistsError('Preserve existing output: ' + str(campaign / name))


CLIENT = """import {readFileSync,writeFileSync} from 'node:fs';
const [wireFile,index,url,output,timeout]=process.argv.slice(2),row=JSON.parse(readFileSync(wireFile,'utf8'))[Number(index)];
const started=performance.now();let result={request_id:row.request_id,http_attempts:1};
try{const response=await fetch(url+'/add',{method:'POST',headers:{'content-type':'application/json'},body:row.body,signal:AbortSignal.timeout(Number(timeout))});
const body=await response.text();result.http_status=response.status;try{result.response=JSON.parse(body);}catch{result.response_text=body;}}
catch(error){result.transport_error={name:error.name,message:error.message};}
result.elapsed_ms=performance.now()-started;writeFileSync(output,JSON.stringify(result)+'\\n',{flag:'wx',mode:0o600});
"""


def launcher(dist):
    return """import {buildServer} from DIST;
const config=JSON.parse(process.env.SERVICE_CONFIG_JSON);config.llmKey=process.env.MEMORY_LLM_API_KEY;
const app=await buildServer(config);await app.listen({host:'127.0.0.1',port:config.port});
for(const signal of ['SIGINT','SIGTERM'])process.once(signal,()=>{void app.close();});
""".replace('DIST', json.dumps((dist / 'server.js').as_uri()))


def prepare(args):
    campaign = ROOT / 'artifacts' / tail.name(args.campaign)
    fresh(campaign, planning=True)
    data, original, wire, receipts = validate_inputs(args.inputs, args.inputs_sha256)
    dist, hashes, provenance = candidate(args.candidate_dist, args.candidate_manifest, args.expected_service_commit)
    old_ports = {read_json(data['original_service_config'])['port']}
    recovery_config = Path(data['previous_recovery']) / 'service.json'
    if recovery_config.exists():
        old_ports.add(read_json(recovery_config)['port'])
    if not 1024 <= args.port <= 65535 or args.port in old_ports:
        raise ValueError('An independent valid service port is required')
    campaign.mkdir(mode=0o700, exist_ok=True)
    shutil.copytree(dist, campaign / 'service-dist')
    if all_hashes(campaign / 'service-dist') != hashes or all_hashes(dist) != hashes:
        raise ValueError('Candidate changed during freezing')
    shutil.copy2(args.candidate_manifest, campaign / 'candidate-manifest.json')
    shutil.copy2(args.inputs, campaign / 'frozen-inputs.private.json')
    write_json(campaign / 'wire-requests.private.json', wire)
    write_json(campaign / 'prefix-receipts.private.json', receipts)
    (campaign / 'service-launcher.mjs').write_text(launcher(campaign / 'service-dist'))
    (campaign / 'http-client.mjs').write_text(CLIENT)
    (campaign / 'node_modules').symlink_to(Path(original['code_root']) / 'service/node_modules', target_is_directory=True)
    write_json(campaign / 'package.json', {'type': 'module'})
    execution = [Path(__file__).resolve(), Path(tail.__file__), ROOT / 'scripts/experiment_runtime.py',
                 campaign / 'service-launcher.mjs', campaign / 'http-client.mjs']
    plan = {'protocol': 'fixed-failed-adds-http-v1', 'created_at': now(), 'campaign': campaign.name, 'scope': SCOPE,
            'inputs': str(campaign / 'frozen-inputs.private.json'), 'inputs_source': str(args.inputs.resolve()),
            'inputs_sha256': args.inputs_sha256, 'candidate_dist': str(campaign / 'service-dist'),
            'candidate_manifest': str(campaign / 'candidate-manifest.json'), 'candidate_provenance': provenance,
            'candidate_files': hashes, 'port': args.port, 'data_dir': str(campaign / 'data'),
            'planned_adds': len(data['cases']), 'prefix_receipts': len(receipts),
            'wire_sha256': sha((campaign / 'wire-requests.private.json').read_bytes()),
            'prefix_receipts_sha256': sha((campaign / 'prefix-receipts.private.json').read_bytes()),
            'execution_files': {str(p): sha(p.read_bytes()) for p in execution}}
    write_json(campaign / 'plan.json', plan)
    result = validate(campaign)
    write_json(campaign / 'validation.json', result)
    return result


def validate(campaign, require_idle=False, allow_owned_launch=False):
    plan = read_json(campaign / 'plan.json')
    if plan['protocol'] != 'fixed-failed-adds-http-v1' or plan['campaign'] != campaign.name:
        raise ValueError('Plan identity changed')
    fresh(campaign, allow_owned_launch)
    data, _, wire, receipts = validate_inputs(plan['inputs'], plan['inputs_sha256'])
    if sha(Path(plan['inputs_source']).read_bytes()) != plan['inputs_sha256']:
        raise ValueError('Original immutable input changed')
    _, hashes, provenance = candidate(plan['candidate_dist'], plan['candidate_manifest'], plan['candidate_provenance']['commit'])
    if hashes != plan['candidate_files'] or provenance != plan['candidate_provenance']:
        raise ValueError('Frozen candidate provenance changed')
    for filename, key, expected in [('wire-requests.private.json', 'wire_sha256', wire), ('prefix-receipts.private.json', 'prefix_receipts_sha256', receipts)]:
        if sha((campaign / filename).read_bytes()) != plan[key] or read_json(campaign / filename) != expected:
            raise ValueError('Frozen request or receipt evidence changed')
    if len(data['cases']) != plan['planned_adds'] or len(receipts) != plan['prefix_receipts']:
        raise ValueError('Planned add or prefix count changed')
    for filename, expected in plan['execution_files'].items():
        if sha(Path(filename).read_bytes()) != expected:
            raise ValueError('Reviewed execution code changed')
    return {'protocol': plan['protocol'], 'validation': 'passed', 'scope': SCOPE,
            'planned_adds': len(data['cases']), 'prefix_receipts_verified': len(receipts),
            'candidate_commit': provenance['commit'], 'compiled_files': len(hashes), 'model_calls_made': 0,
            **idle(data, require_idle)}


def database_result(path, case, prefix, result):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        meta = dict(db.execute('SELECT key,value FROM meta'))
        saved = {rid: {'hash': digest, 'receipt': json.loads(receipt)} for rid, digest, receipt in db.execute('SELECT id,hash,receipt FROM requests')}
        integrity = db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    user = case['request']['user_id']
    original = {rid: row for rid, row in prefix.items() if rid.startswith(user + ':')}
    successful = result.get('http_status') == 200
    wanted = set(original) | ({case['request_id']} if successful else set())
    receipt = saved.get(case['request_id'])
    checks = {'sqlite_integrity': integrity, 'tenant_unchanged': meta.get('user_id') == user,
              'single_matching_http_result': result.get('request_id') == case['request_id'] and result.get('http_attempts') == 1,
              'prefix_receipts_unchanged': all(saved.get(rid) == row for rid, row in original.items()),
              'exact_receipt_set': set(saved) == wanted,
              'revision_transition': int(meta.get('revision', -1)) == case['revision'] + int(successful),
              'failed_request_absent_or_success_receipt_matches': (receipt == {'hash': case['request_sha256'], 'receipt': result.get('response')}) if successful else receipt is None}
    logical = {}
    if not successful:
        before_state, after_state = logical_state(case['snapshot']), logical_state(path)
        checks['failed_write_logical_state_unchanged'] = before_state == after_state
        logical = {'before': sha(json.dumps(before_state, sort_keys=True).encode()), 'after': sha(json.dumps(after_state, sort_keys=True).encode())}
    return {'sample_id': case['sample_id'], 'request_id': case['request_id'], 'http_status': result.get('http_status'),
            'elapsed_ms': result.get('elapsed_ms'), 'http_attempts': result.get('http_attempts'),
            'error_code': result.get('response', {}).get('error', {}).get('code') if isinstance(result.get('response'), dict) else None,
            'transport_error': bool(result.get('transport_error')), 'revision_before': case['revision'],
            'revision_after': int(meta.get('revision', -1)), 'receipt_present': receipt is not None,
            'checks': checks, 'failed_write_logical_hashes': logical, 'integrity': 'pass' if all(checks.values()) else 'fail'}


def logical_state(path):
    """Stable database content hashes; checkpoint/layout changes are irrelevant.

    Sort encoded complete rows, preserving duplicate rows and SQLite BLOB types.
    Only schema text, counts and digests leave this function, never private rows.
    """
    encode = lambda row: json.dumps([{'blob_hex': x.hex()} if isinstance(x, bytes) else x for x in row], ensure_ascii=False, separators=(',', ':'))
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        schema = list(db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name,tbl_name"))
        tables = [r[1] for r in schema if r[0] == 'table']
        result = {'schema_sha256': sha(encode(schema).encode()), 'tables': {}}
        for table in tables:
            rows = sorted(encode(row) for row in db.execute('SELECT * FROM "' + table.replace('"', '""') + '"'))
            result['tables'][table] = {'rows': len(rows), 'sha256': sha(json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode())}
    return result


def execution_env(inputs, plan):
    env = os.environ.copy()
    for line in Path(inputs['env_file']).read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1); env[key.strip()] = value.strip().strip('"').strip("'")
    original = read_json(Path(inputs['origin']) / 'plan.json')
    spec = read_json(original['spec'])
    env.update(spec['defaults']); env.update(spec['profiles']['candidate']['environment'])
    config = read_json(inputs['original_service_config'])
    config.update(dataDir=plan['data_dir'], port=plan['port'], host='127.0.0.1',
                  failed_add_execution={'scope': SCOPE, 'provenance': plan['candidate_provenance'], 'artifact': plan['candidate_dist'],
                                        'compiled_files': plan['candidate_files'], 'inputs_sha256': plan['inputs_sha256'],
                                        'historical_prefixes': [{k:c[k] for k in ['sample_id','revision','snapshot_sha256']} for c in inputs['cases']]})
    config.pop('llmKey', None)
    env['SERVICE_CONFIG_JSON'] = json.dumps(config, separators=(',', ':'))
    env['SERVICE_CONFIG_SHA256'] = sha(env['SERVICE_CONFIG_JSON'].encode())
    campaign = Path(plan['candidate_dist']).parent
    env.update(MEMORY_MODEL_AUDIT=str(campaign / 'model-calls.jsonl'), MEMORY_MODEL_TRACE=str(campaign / 'model-trace.jsonl'),
               MEMORY_RETRIEVAL_AUDIT=str(campaign / 'retrieval-stages.jsonl'), MEMORY_LLM_BASE_URL=config['llmBase'])
    return env, config


def stop_owned(runtime):
    # Stop only child handles created by this ManagedRun, never saved PIDs.
    for role, child in runtime.processes.items():
        if child.poll() is None:
            runtime.state['children'][role]['stopped_by_runner'] = True
            child.terminate()
            try: child.wait(timeout=15)
            except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)
        runtime.state['children'][role]['required'] = False
    runtime.poll(check_required=False)


def summarize(campaign, plan, data, results):
    prefix = read_json(campaign / 'prefix-receipts.private.json')
    observed = []
    for index, case in enumerate(data['cases']):
        result = results[index] if index < len(results) else {}
        path = Path(plan['data_dir']) / sha(case['request']['user_id'].encode()) / 'memory.sqlite'
        try: row = database_result(path, case, prefix, result)
        except (OSError, sqlite3.Error, ValueError):
            row = {'sample_id': case['sample_id'], 'request_id': case['request_id'], 'integrity': 'fail', 'database_validation': 'unavailable'}
        observed.append(row)
    snapshots_ok = all(sha(Path(c['snapshot']).read_bytes()) == c['snapshot_sha256'] for c in data['cases'])
    frozen_ok = all_hashes(Path(plan['candidate_dist'])) == plan['candidate_files'] and all(sha(Path(plan[k]).read_bytes()) == plan['inputs_sha256'] for k in ['inputs', 'inputs_source'])
    traces = {r.get('trace_id'): r.get('identity', {}).get('request_id') for r in tail.rows(campaign / 'model-trace.jsonl')}
    stages = Counter((traces.get(r.get('trace_id')), r.get('purpose'), r.get('outcome')) for r in tail.rows(campaign / 'model-calls.jsonl') if r.get('kind') == 'generation')
    allowed = {c['request_id'] for c in data['cases']}
    prefix_calls = sum(n for (rid, _, _), n in stages.items() if rid in prefix)
    unknown_calls = sum(n for (rid, _, _), n in stages.items() if rid not in allowed and rid not in prefix)
    degraded = set()
    if (campaign / 'service.log').exists():
        for line in (campaign / 'service.log').read_text().splitlines():
            try: row = json.loads(line)
            except json.JSONDecodeError: continue
            if row.get('event') == 'degraded': degraded.add(row.get('request_id'))
    for row in observed: row['successful_degraded_add'] = row['request_id'] in degraded
    result = {'protocol': 'fixed-failed-adds-http-result-v1', 'scope': SCOPE, 'at': now(), 'candidate_provenance': plan['candidate_provenance'],
              'results': observed, 'snapshots_unchanged': snapshots_ok, 'frozen_inputs_and_dist_unchanged': frozen_ok,
              'planned_adds': plan['planned_adds'], 'completed_http_attempts': len(results), 'unknown_generation_calls': unknown_calls, 'prefix_generation_calls': prefix_calls,
              'successful_degraded_adds': len(degraded),
              'model_stages': [{'request_id': rid, 'purpose': p, 'outcome': o, 'attempts': n} for (rid, p, o), n in stages.items()],
              'degradation_counter_scope': 'Service degraded events describe successful committed writes only; failed prepare phases may have degraded before rollback.'}
    result['integrity'] = 'pass' if snapshots_ok and frozen_ok and not (unknown_calls or prefix_calls) and len(results) == plan['planned_adds'] and all(r['integrity'] == 'pass' for r in observed) else 'fail'
    result['all_adds_succeeded'] = all(r.get('http_status') == 200 for r in observed) and len(results) == plan['planned_adds']
    write_json(campaign / 'summary.json', result)
    return result


def execute(campaign):
    validate(campaign, require_idle=True, allow_owned_launch=True)
    plan = read_json(campaign / 'plan.json'); data = read_json(plan['inputs'])
    env, config = execution_env(data, plan)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', plan['port']))
    results = []
    with ManagedRun(campaign / 'runtime.json') as runtime:
        failure = None
        try:
            with ExitStack() as files:
                idle(data, require=True)
                state_dir = Path(plan['data_dir']); state_dir.mkdir()
                (campaign / 'http-results').mkdir()
                for case in data['cases']:
                    dest = state_dir / sha(case['request']['user_id'].encode()); dest.mkdir()
                    shutil.copy2(case['snapshot'], dest / 'memory.sqlite')
                    if sha((dest / 'memory.sqlite').read_bytes()) != case['snapshot_sha256']:
                        raise ValueError('Snapshot changed during restoration')
                if all_hashes(Path(plan['candidate_dist'])) != plan['candidate_files'] or any(sha(Path(f).read_bytes()) != h for f, h in plan['execution_files'].items()):
                    raise ValueError('Frozen execution code changed before service launch')
                write_json(campaign / 'service.json', config)
                if sys.platform == 'darwin':
                    runtime.spawn('sleep-prevention', ['/usr/bin/caffeinate', '-i', '-w', str(os.getpid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                service = runtime.spawn('service', ['node', str(campaign / 'service-launcher.mjs')], required=True, cwd=campaign, env=env,
                                        stdin=subprocess.DEVNULL, stdout=tail.private_log(files, campaign / 'service.log'), stderr=subprocess.STDOUT)
                base = 'http://127.0.0.1:' + str(plan['port'])
                for _ in range(80):
                    runtime.poll()
                    try:
                        with urllib.request.urlopen(base + '/health', timeout=1) as response:
                            if response.status == 200: break
                    except Exception: time.sleep(.25)
                else: raise RuntimeError('Owned service did not become ready')
                for index, case in enumerate(data['cases']):
                    runtime.poll(); idle(data, require=True)
                    output = campaign / 'http-results' / (str(index) + '.private.json')
                    job = runtime.spawn('http-add-' + str(index), ['node', str(campaign / 'http-client.mjs'), str(campaign / 'wire-requests.private.json'),
                                        str(index), base, str(output), str(config['addTimeout'] + 1000)], cwd=campaign, env=env,
                                        stdin=subprocess.DEVNULL, stdout=tail.private_log(files, campaign / ('http-' + str(index) + '.log')), stderr=subprocess.STDOUT)
                    while job.poll() is None:
                        runtime.poll(); time.sleep(.2)
                    runtime.poll()
                    if job.returncode or not output.exists(): raise RuntimeError('Owned single-attempt HTTP client failed')
                    result = read_json(output); results.append(result)
                    write_json(campaign / 'results.private.json', results)
                    print(json.dumps({'sample_id': case['sample_id'], 'http_status': result.get('http_status'), 'elapsed_ms': result.get('elapsed_ms')}), flush=True)
        except BaseException as error:
            failure = error
        finally:
            # Inspect final revision/receipts only after every owned child exits.
            stop_owned(runtime)
        summary = summarize(campaign, plan, data, results)
        if failure is not None: raise failure
        if summary['integrity'] != 'pass' or not summary['all_adds_succeeded']:
            raise RuntimeError('Single-attempt add validation failed; preserve the recorded outcomes')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', required=True)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--plan', action='store_true'); actions.add_argument('--validate', action='store_true'); actions.add_argument('--run', action='store_true')
    parser.add_argument('--inputs', type=Path, default=INPUTS); parser.add_argument('--inputs-sha256', default=INPUT_SHA)
    parser.add_argument('--candidate-dist', type=Path); parser.add_argument('--candidate-manifest', type=Path); parser.add_argument('--expected-service-commit')
    parser.add_argument('--port', type=int, default=8117); parser.add_argument('--detach', action='store_true')
    args = parser.parse_args(); os.umask(0o077)
    campaign = ROOT / 'artifacts' / tail.name(args.campaign)
    if args.detach and not args.run: parser.error('--detach requires --run')
    if args.plan: print(json.dumps(prepare(args), indent=2))
    elif args.validate: print(json.dumps(validate(campaign), indent=2))
    elif args.detach:
        validate(campaign, require_idle=True)
        print(json.dumps(launch_detached([sys.executable, str(Path(__file__).resolve()), '--campaign', args.campaign, '--run'], campaign / 'runtime.json', cwd=ROOT), indent=2))
    else: execute(campaign)


if __name__ == '__main__':
    try: main()
    except RunInterrupted as error: sys.exit(128 + error.signum)
