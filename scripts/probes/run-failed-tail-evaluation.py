"""Recover failed MemOps tails through the unchanged frozen HTTP evaluator.

--plan and --preflight never start a service or call a model. --run requires a
stopped origin controller and its completed gate. This is mixed-version history
recovery, not a fresh benchmark or a retrieval-only ablation.
"""
import argparse
from collections import Counter
from contextlib import ExitStack, closing
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from experiment_runtime import ManagedRun, RunInterrupted, launch_detached, now, read_status, write_json

EVAL_COMMIT = '7a2ecb42976220e70ff450d30c9569552a8ee1ab'
SAMPLES = [('B14_forget', 43, 'segment-43:0', 5), ('A06_forget', 37, 'segment-37:0', 5),
           ('B01_remember', 36, 'segment-36:0', 6)]
QUESTIONS = sum(s[3] for s in SAMPLES)
PREFIX_COUNT = sum(s[1] for s in SAMPLES)
SCOPE = ('Targeted recovery with original-version historical prefixes and candidate-version tails; '
         f'all {QUESTIONS} questions use the normal HTTP evaluator. Not fresh ingestion, a 972-question score, '
         'or a retrieval-only ablation. Original results remain unchanged.')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def rows(path):
    if not Path(path).exists():
        return []
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def name(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value):
        raise ValueError('Campaign must be a simple new directory name')
    return value


def fresh_outputs(campaign, evaluation, allow_owned_launch=False):
    for path in [campaign / 'runtime.json', campaign / 'runtime.launch.json', campaign / 'runtime.owner',
                 campaign / 'data', campaign / 'service.log', campaign / 'summary.json', evaluation]:
        if path.exists():
            if path.name == 'runtime.launch.json' and allow_owned_launch:
                # A detached child may consume its own launch record exactly once.
                for _ in range(10):
                    try:
                        launch = read_json(path)
                        break
                    except json.JSONDecodeError:
                        time.sleep(.05)
                else:
                    raise ValueError('Incomplete detached launch record')
                if launch.get('pid') == os.getpid():
                    continue
            raise FileExistsError(f'Preserve existing output: {path}')


def origin_state(origin, require_idle=False):
    runtime = origin / 'runtime.json'
    if not runtime.exists():
        raise ValueError('Origin controller ownership record is missing')
    state = read_status(runtime)
    blockers = []
    if state.get('alive') or any(c.get('alive') for c in state.get('children', {}).values()):
        blockers.append('origin_controller_or_owned_child_alive')
    if state['status'] not in ('finished', 'failed', 'interrupted'):
        blockers.append('origin_controller_not_terminal')
    gate = read_json(origin / 'gate.json') if (origin / 'gate.json').exists() else None
    if not gate or gate.get('status') not in ('pass', 'fail'):
        blockers.append('origin_gate_missing_or_incomplete')
    result = {'cloud_start_allowed': not blockers, 'blockers': blockers,
              'origin_runtime_status': state['status'], 'origin_gate_status': gate.get('status') if gate else None}
    if require_idle and blockers:
        raise ValueError('Cloud execution blocked: ' + ', '.join(blockers))
    return result


def pinned_origin(origin):
    plan = read_json(origin / 'plan.json')
    if plan['source_checkpoints']['eval']['commit'] != EVAL_COMMIT:
        raise ValueError('Recovery requires the frozen evaluator 7a2ecb4')
    code = Path(plan['code_root'])
    for relative, expected in plan['frozen_code_hashes'].items():
        if Path(relative).is_absolute() or '..' in Path(relative).parts:
            raise ValueError('Invalid frozen artifact path')
        if sha((code / relative).read_bytes()) != expected:
            raise ValueError(f'Origin frozen artifact changed: {relative}')
    if sha(Path(plan['spec']).read_bytes()) != plan['spec_sha256']:
        raise ValueError('Original model/evaluation spec changed')
    phase = next(p for p in plan['phases'] if p['id'] == 'memops-risk')
    raw = Path(phase['data_file']).read_bytes()
    if sha(raw) != phase['dataset_sha256']:
        raise ValueError('Original risk dataset changed')
    all_samples = json.loads(raw)
    selected = [next(s for s in all_samples if s['sample_id'] == sample) for sample, _, _, _ in SAMPLES]
    if any(s['benchmark'] != 'memops' or len(s['questions']) != case[3] for s, case in zip(selected, SAMPLES)):
        raise ValueError('Recovery requires all complete selected MemOps samples')
    spec = read_json(plan['spec'])
    if spec['evaluation']['concurrency'] != 1:
        raise ValueError('Original spec must be single-worker')
    return plan, selected


def request_schedule(eval_root, data_file, namespace):
    # Use the frozen evaluator's actual chunking and JS request serialization.
    program = """import {readFileSync} from 'node:fs';import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
const {chunks}=await import(pathToFileURL(process.argv[1]));
const samples=JSON.parse(readFileSync(process.argv[2],'utf8')),namespace=process.argv[3],out=[];
for(const sample of samples){const user_id=`${namespace}:${sample.benchmark}:${sample.sample_id}`;
for(const session of sample.sessions)for(const [part,messages] of chunks(session.messages,20,2000).entries()){
const request_id=`${user_id}:${session.session_id}:${part}`,req={request_id,user_id,session_id:session.session_id,messages};
out.push({sample_id:sample.sample_id,request_id,hash:createHash('sha256').update(JSON.stringify(req)).digest('hex')});}}
console.log(JSON.stringify(out));"""
    return json.loads(subprocess.check_output(['node', '--input-type=module', '-e', program,
        str(eval_root / 'dist/adapters.js'), str(data_file), namespace], text=True))


def snapshot_receipts(path, schedule, namespace, sample, revision, failed_suffix):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        meta = dict(db.execute('SELECT key,value FROM meta'))
        saved = {rid: {'hash': digest, 'receipt': json.loads(receipt)}
                 for rid, digest, receipt in db.execute('SELECT id,hash,receipt FROM requests')}
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('Snapshot failed SQLite integrity validation')
    user = f'{namespace}:memops:{sample}'
    if meta.get('user_id') != user or int(meta.get('revision', -1)) != revision:
        raise ValueError('Snapshot tenant or pre-failure revision mismatch')
    if meta.get('source_format') != 'dual-source-v5-s1':
        raise ValueError('Snapshot source format differs from original v5 history')
    expected = [r for r in schedule if r['sample_id'] == sample]
    if len(saved) != revision or set(saved) != {r['request_id'] for r in expected[:revision]}:
        raise ValueError('Snapshot receipts are not the complete exact pre-failure prefix')
    if expected[revision]['request_id'] != f'{user}:{failed_suffix}':
        raise ValueError('Snapshot does not stop immediately before the original failed request')
    for request in expected[:revision]:
        if saved[request['request_id']]['hash'] != request['hash']:
            raise ValueError('Snapshot receipt payload differs from the unchanged HTTP request')
    return saved


def dist_hashes(directory):
    return {str(p.relative_to(directory)): sha(p.read_bytes()) for p in sorted(directory.rglob('*.js'))}


def candidate_input(code_root=None, dist=None, manifest=None, expected_commit=None):
    provenance = {'kind': 'compiled-artifact'}
    if code_root:
        service = Path(code_root).resolve()
        if (service / 'service/dist').exists():
            service = service / 'service'
        dist = service / 'dist'
        provenance.update(kind='service-code-root', path=str(service))
        try:
            provenance.update(commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=service, text=True).strip(),
                              tracked_diff_sha256=sha(subprocess.check_output(['git', 'diff', 'HEAD'], cwd=service)))
        except subprocess.SubprocessError:
            raise ValueError('Candidate code_root must identify a service repository')
        provenance['source_hashes'] = {str(p.relative_to(service)): sha(p.read_bytes())
                                      for p in sorted((service / 'src').rglob('*.ts'))}
    elif not dist or not manifest:
        raise ValueError('Use candidate-code-root or both candidate-dist and candidate-manifest')
    dist = Path(dist).resolve()
    actual = dist_hashes(dist)
    if any(required not in actual for required in ['server.js', 'config.js', 'models.js', 'storage.js']):
        raise ValueError('Candidate dist is incomplete')
    if manifest:
        stated = read_json(manifest)
        declared = stated.get('dist_hashes', stated.get('dist'))
        if not isinstance(declared, dict) or actual != {k: v for k, v in declared.items() if k.endswith('.js')}:
            raise ValueError('Candidate dist differs from supplied hash manifest')
        for relative, expected in declared.items():
            if Path(relative).is_absolute() or '..' in Path(relative).parts or sha((dist / relative).read_bytes()) != expected:
                raise ValueError('Candidate manifest file hash mismatch')
        provenance['manifest_sha256'] = sha(Path(manifest).read_bytes())
        provenance['commit'] = stated.get('commit', provenance.get('commit'))
    if expected_commit and provenance.get('commit') != expected_commit:
        raise ValueError('Candidate commit differs from the explicitly requested commit')
    return dist, actual, provenance


def prepare(args):
    campaign = ROOT / 'artifacts' / name(args.campaign)
    if campaign.exists():
        raise FileExistsError('Preserve existing campaign; choose a new recovery ID')
    origin = ROOT / 'artifacts' / name(args.origin)
    original, samples = pinned_origin(origin)
    dist, hashes, provenance = candidate_input(args.candidate_code_root, args.candidate_dist, args.candidate_manifest,
                                              args.expected_service_commit)
    if not 1024 <= args.port <= 65535 or args.port == original['port']:
        raise ValueError('Recovery needs a separate valid service port')
    evaluation = ROOT / 'eval/artifacts' / (args.campaign + '-candidate-memops-tail')
    fresh_outputs(campaign, evaluation)
    campaign.mkdir(mode=0o700)
    data_file = campaign / 'samples.json'
    write_json(data_file, samples)
    code = Path(original['code_root'])
    schedule = request_schedule(code / 'eval', data_file, original['namespace'])
    snapshots, receipts = [], {}
    defaults = [ROOT / 'artifacts/priority-full-B14-forget-witness-20260908/original-readonly-snapshot.sqlite',
                ROOT / 'artifacts/priority-full-A06-forget-witness-20260908/before.sqlite',
                ROOT / 'artifacts/priority-full-B01-remember-tail-snapshot-20260908/before.sqlite']
    supplied = [args.b14_snapshot, args.a06_snapshot, args.b01_snapshot]
    (campaign / 'snapshots').mkdir()
    for (sample, revision, suffix, _), path, fallback in zip(SAMPLES, supplied, defaults):
        source = (path or fallback).resolve()
        records = snapshot_receipts(source, schedule, original['namespace'], sample, revision, suffix)
        destination = campaign / 'snapshots' / (sample + '.sqlite')
        shutil.copy2(source, destination)
        if sha(source.read_bytes()) != sha(destination.read_bytes()):
            raise ValueError('Snapshot changed during capture')
        snapshots.append({'sample_id': sample, 'revision': revision, 'failed_suffix': suffix,
                          'path': str(destination), 'sha256': sha(destination.read_bytes()),
                          'source': str(source), 'receipts': len(records)})
        receipts.update(records)
    target_dist = campaign / 'service-dist'
    shutil.copytree(dist, target_dist)
    if dist_hashes(target_dist) != hashes or dist_hashes(dist) != hashes:
        raise ValueError('Candidate changed while its execution artifact was being frozen')
    (campaign / 'node_modules').symlink_to(code / 'service/node_modules', target_is_directory=True)
    write_json(campaign / 'package.json', {'type': 'module'})
    write_json(campaign / 'expected-prefix-receipts.private.json', receipts)
    write_json(campaign / 'schedule.json', schedule)
    plan = {'protocol': 'failed-tail-http-recovery-v1', 'campaign': args.campaign, 'created_at': now(), 'scope': SCOPE,
            'origin': str(origin), 'origin_plan_sha256': sha((origin / 'plan.json').read_bytes()),
            'origin_service_config_sha256': sha((origin / 'service.json').read_bytes()),
            'namespace': original['namespace'], 'eval_code_root': str(code / 'eval'), 'eval_commit': EVAL_COMMIT,
            'spec': original['spec'], 'spec_sha256': original['spec_sha256'], 'env_file': original['env_file'],
            'dataset': str(data_file), 'dataset_sha256': sha(data_file.read_bytes()), 'planned_questions': QUESTIONS,
            'run_id': args.campaign + '-candidate-memops-tail', 'run_dir': str(evaluation), 'port': args.port,
            'data_dir': str(campaign / 'data'), 'snapshots': snapshots, 'expected_prefix_receipts': len(receipts),
            'expected_total_adds': len(schedule), 'expected_new_adds': len(schedule) - len(receipts),
            'candidate_dist': str(target_dist), 'dist_hashes': hashes, 'candidate_provenance': provenance,
            'prefix_receipts_sha256': sha((campaign / 'expected-prefix-receipts.private.json').read_bytes()),
            'schedule_sha256': sha((campaign / 'schedule.json').read_bytes()),
            'provenance_note': 'Evaluator checkout git metadata describes its original frozen checkout. '
                               'The executed service artifact is independently pinned in service_configuration.recovery_execution.'}
    write_json(campaign / 'plan.json', plan)
    return preflight(campaign)


def preflight(campaign, require_idle=False, allow_owned_launch=False):
    plan = read_json(campaign / 'plan.json')
    if plan['protocol'] != 'failed-tail-http-recovery-v1' or plan['campaign'] != campaign.name:
        raise ValueError('Recovery plan identity mismatch')
    origin = Path(plan['origin'])
    original, selected = pinned_origin(origin)
    if sha((origin / 'plan.json').read_bytes()) != plan['origin_plan_sha256']:
        raise ValueError('Origin plan changed')
    if sha((origin / 'service.json').read_bytes()) != plan['origin_service_config_sha256']:
        raise ValueError('Origin service configuration changed')
    if plan['namespace'] != original['namespace'] or plan['planned_questions'] != QUESTIONS:
        raise ValueError('Recovery namespace or denominator changed')
    if read_json(plan['dataset']) != selected or sha(Path(plan['dataset']).read_bytes()) != plan['dataset_sha256']:
        raise ValueError('Complete sample history or questions changed')
    if sha(Path(plan['spec']).read_bytes()) != plan['spec_sha256'] or plan['spec_sha256'] != original['spec_sha256']:
        raise ValueError('Original spec changed')
    if dist_hashes(Path(plan['candidate_dist'])) != plan['dist_hashes']:
        raise ValueError('Frozen recovery service artifact changed')
    for filename, key in [('schedule.json', 'schedule_sha256'), ('expected-prefix-receipts.private.json', 'prefix_receipts_sha256')]:
        if sha((campaign / filename).read_bytes()) != plan[key]:
            raise ValueError(f'Pinned recovery evidence changed: {filename}')
    schedule = request_schedule(Path(plan['eval_code_root']), Path(plan['dataset']), plan['namespace'])
    if schedule != read_json(campaign / 'schedule.json'):
        raise ValueError('Frozen evaluator request schedule changed')
    receipts = {}
    for snapshot in plan['snapshots']:
        if sha(Path(snapshot['path']).read_bytes()) != snapshot['sha256']:
            raise ValueError('Pre-failure snapshot changed')
        receipts.update(snapshot_receipts(snapshot['path'], schedule, plan['namespace'], snapshot['sample_id'],
                                          snapshot['revision'], snapshot['failed_suffix']))
    if receipts != read_json(campaign / 'expected-prefix-receipts.private.json') or len(receipts) != PREFIX_COUNT:
        raise ValueError('The original prefix receipts changed')
    fresh_outputs(campaign, Path(plan['run_dir']), allow_owned_launch=allow_owned_launch)
    status = origin_state(origin, require_idle=require_idle)
    return {'protocol': plan['protocol'], 'campaign': campaign.name, 'preflight': 'passed', 'scope': SCOPE,
            'planned_questions': QUESTIONS, 'prefix_http_receipts': len(receipts), 'new_tail_adds': len(schedule)-len(receipts),
            'model_calls_made': 0, **status}


def private_log(files, path):
    stream = files.enter_context(path.open('x'))
    os.chmod(path, 0o600)
    return stream


def service_launcher(campaign, dist):
    # Observe HTTP responses; no store reads and no changed evaluator behavior.
    return """import {readFileSync,appendFileSync} from 'node:fs';
import {isDeepStrictEqual} from 'node:util';
import {buildServer} from DIST;
const config=JSON.parse(process.env.SERVICE_CONFIG_JSON);config.llmKey=process.env.MEMORY_LLM_API_KEY;
const expected=JSON.parse(readFileSync(PREFIX,'utf8')),app=await buildServer(config);
app.addHook('onSend',(request,reply,payload,done)=>{
if(request.routeOptions.url==='/add'){
const request_id=request.body?.request_id,prefix=Object.hasOwn(expected,request_id);let matches=null;
if(prefix){try{matches=reply.statusCode===200&&isDeepStrictEqual(JSON.parse(String(payload)),expected[request_id].receipt);}catch{matches=false;}}
appendFileSync(AUDIT,JSON.stringify({at:new Date().toISOString(),event:'recovery_http_receipt',request_id,
classification:prefix?'prefix':'tail',status:reply.statusCode,matches_snapshot:matches})+'\\n',{mode:0o600});
}done(null,payload);});
await app.listen({host:'127.0.0.1',port:config.port});
for(const signal of ['SIGINT','SIGTERM'])process.once(signal,()=>{void app.close();});
""".replace('DIST', json.dumps((dist / 'server.js').as_uri())).replace('PREFIX', json.dumps(str(campaign / 'expected-prefix-receipts.private.json'))).replace('AUDIT', json.dumps(str(campaign / 'http-receipts.jsonl')))


def summarize(campaign, plan):
    directory = Path(plan['run_dir'])
    ingest, judgments = rows(directory / 'ingest.jsonl'), rows(directory / 'judgments.jsonl')
    latest = {r['request_id']: r for r in ingest}
    receipt_rows = rows(campaign / 'http-receipts.jsonl')
    prefix = {r['request_id']: r for r in receipt_rows if r['classification'] == 'prefix'}
    expected_prefix = set(read_json(campaign / 'expected-prefix-receipts.private.json'))
    expected = {r['request_id'] for r in read_json(campaign / 'schedule.json')}
    stages, prefix_calls, unknown_calls = Counter(), 0, 0
    # Read only trace metadata for classification; never emit prompts or output.
    identities = {r.get('trace_id'): r.get('identity', {}).get('request_id')
                  for r in rows(campaign / 'model-trace.jsonl')}
    for row in rows(campaign / 'model-calls.jsonl'):
        if row.get('kind') != 'generation' or row.get('purpose') == 'rerank':
            continue
        rid = identities.get(row.get('trace_id'))
        if rid in expected_prefix:
            prefix_calls += 1
        elif rid in expected:
            stages[(row.get('purpose'), row.get('outcome'))] += 1
        else:
            unknown_calls += 1
    service_rows = []
    if (campaign / 'service.log').exists():
        for line in (campaign / 'service.log').read_text().splitlines():
            try:
                service_rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    degraded = {r['request_id'] for r in service_rows if r.get('event') == 'degraded'}
    reasons = []
    if set(prefix) != expected_prefix or any(not r['matches_snapshot'] for r in prefix.values()):
        reasons.append('prefix_HTTP_receipts_missing_or_changed')
    if prefix_calls or unknown_calls:
        reasons.append('unexpected_write_model_identity')
    if set(latest) != expected or any(r.get('status') != 'ok' for r in latest.values()):
        reasons.append('tail_ingestion_failed_or_incomplete')
    qids = [q['qid'] for s in read_json(plan['dataset']) for q in s['questions']]
    if len(judgments) != QUESTIONS or {r['qid'] for r in judgments} != set(qids):
        reasons.append('question_records_incomplete_or_duplicated')
    manifest = read_json(directory / 'manifest.json') if (directory / 'manifest.json').exists() else {}
    for key, expected_value in [('status', 'finished'), ('run_id', plan['run_id']), ('memory_namespace', plan['namespace']),
                                ('planned_questions', QUESTIONS), ('dataset_sha256', plan['dataset_sha256']), ('eval_commit', EVAL_COMMIT)]:
        if manifest.get(key) != expected_value:
            reasons.append('manifest_mismatch:' + key)
    correct = sum(r.get('status') == 'judged' and r.get('correct') is True for r in judgments)
    return {'protocol': 'failed-tail-http-recovery-result-v1', 'scope': SCOPE, 'at': now(),
            'integrity': 'fail' if reasons else 'pass', 'reasons': reasons, 'planned_questions': QUESTIONS,
            'correct': correct, 'accuracy_over_planned': correct/QUESTIONS,
            'question_status_counts': dict(Counter(r.get('status') for r in judgments)),
            'prefix_HTTP_receipts_expected': PREFIX_COUNT, 'prefix_HTTP_receipts_verified': sum(r['matches_snapshot'] is True for r in prefix.values()),
            'prefix_write_model_calls': prefix_calls, 'unknown_write_model_calls': unknown_calls,
            'new_tail_successful_adds': sum(r.get('status') == 'ok' for rid, r in latest.items() if rid not in expected_prefix),
            'final_failed_adds': sum(r.get('status') != 'ok' for r in latest.values()),
            'successful_degraded_adds': len(degraded),
            'degradation_counter_scope': 'Only successful committed writes emit degradation events; failed preparations may have degraded before rollback.',
            'new_write_model_stages': [{'purpose': p, 'outcome': o, 'attempts': n} for (p, o), n in sorted(stages.items())]}


def execute(campaign):
    preflight(campaign, require_idle=True, allow_owned_launch=True)
    plan = read_json(campaign / 'plan.json')
    spec = read_json(plan['spec'])
    env = os.environ.copy()
    for line in Path(plan['env_file']).read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    env.update(spec['defaults'])
    env.update(spec['profiles']['candidate']['environment'])
    for key in ('EVALUATOR_API_BASE', 'EVALUATOR_API_KEY', 'EVALUATOR_MODEL'):
        env.pop(key, None)
    env.update(MEMORY_MODEL_AUDIT=str(campaign / 'model-calls.jsonl'), MEMORY_MODEL_TRACE=str(campaign / 'model-trace.jsonl'),
               MEMORY_RETRIEVAL_AUDIT=str(campaign / 'retrieval-stages.jsonl'))
    safe = read_json(Path(plan['origin']) / 'service.json')
    safe.update(dataDir=plan['data_dir'], port=plan['port'], host='127.0.0.1',
                recovery_execution={'scope': SCOPE, 'artifact': plan['candidate_dist'], 'dist_hashes': plan['dist_hashes'],
                                    'provenance': plan['candidate_provenance'], 'snapshots': plan['snapshots'],
                                    'origin': plan['origin'], 'metadata_limit': plan['provenance_note']})
    safe.pop('llmKey', None)
    env['SERVICE_CONFIG_JSON'] = json.dumps(safe, sort_keys=True, separators=(',', ':'))
    env['SERVICE_CONFIG_SHA256'] = sha(env['SERVICE_CONFIG_JSON'].encode())
    env['MEMORY_LLM_BASE_URL'] = safe['llmBase']
    with socket.socket() as port_probe:
        port_probe.bind(('127.0.0.1', plan['port']))
    with ExitStack() as files, ManagedRun(campaign / 'runtime.json') as runtime:
        # Recheck immediately before any service/model activity.
        origin_state(Path(plan['origin']), require_idle=True)
        state_dir = Path(plan['data_dir']); state_dir.mkdir()
        for snapshot in plan['snapshots']:
            tenant = f'{plan["namespace"]}:memops:{snapshot["sample_id"]}'
            destination = state_dir / sha(tenant.encode()); destination.mkdir()
            shutil.copy2(snapshot['path'], destination / 'memory.sqlite')
        write_json(campaign / 'service.json', safe)
        launcher = campaign / 'service-launcher.mjs'
        launcher.write_text(service_launcher(campaign, Path(plan['candidate_dist'])))
        if sys.platform == 'darwin':
            runtime.spawn('sleep-prevention', ['/usr/bin/caffeinate', '-i', '-w', str(os.getpid())],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        runtime.spawn('service', ['node', str(launcher)], required=True, cwd=campaign, env=env,
                      stdin=subprocess.DEVNULL, stdout=private_log(files, campaign / 'service.log'), stderr=subprocess.STDOUT)
        base = f'http://127.0.0.1:{plan["port"]}'
        for _ in range(80):
            runtime.poll()
            try:
                with urllib.request.urlopen(base + '/health', timeout=1) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(.25)
        else:
            raise RuntimeError('Owned recovery service did not become ready')
        evaluation = spec['evaluation']
        command = ['node', str(Path(plan['eval_code_root']) / 'dist/cli.js'), 'run', '--data', plan['dataset'],
                   '--output', plan['run_dir'], '--run-id', plan['run_id'], '--memory-namespace', plan['namespace'],
                   '--base-url', base, '--concurrency', '1', '--answer-model', evaluation['answer_model'],
                   '--judge-model', evaluation['judge_model'], '--judge-kind', 'rubric', '--mode', 'proxy',
                   '--chunk-messages', '20', '--chunk-words', '2000']
        job = runtime.spawn('memops-tail', command, cwd=plan['eval_code_root'], env=env, stdin=subprocess.DEVNULL,
                            stdout=private_log(files, campaign / 'eval.log'), stderr=subprocess.STDOUT)
        while job.poll() is None:
            runtime.poll(); time.sleep(.2)
        runtime.poll()
        result = summarize(campaign, plan)
        write_json(campaign / 'summary.json', result)
        if job.returncode or result['integrity'] != 'pass':
            raise RuntimeError('Targeted recovery incomplete or failed; preserve its original result')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', required=True)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--plan', action='store_true')
    actions.add_argument('--preflight', action='store_true')
    actions.add_argument('--run', action='store_true')
    parser.add_argument('--origin', default='priority-full-20260908-01')
    candidate = parser.add_mutually_exclusive_group()
    candidate.add_argument('--candidate-code-root', type=Path)
    candidate.add_argument('--candidate-dist', type=Path)
    parser.add_argument('--candidate-manifest', type=Path)
    parser.add_argument('--expected-service-commit')
    parser.add_argument('--b14-snapshot', type=Path)
    parser.add_argument('--a06-snapshot', type=Path)
    parser.add_argument('--b01-snapshot', type=Path)
    parser.add_argument('--port', type=int, default=8116)
    parser.add_argument('--detach', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    campaign = ROOT / 'artifacts' / name(args.campaign)
    if args.detach and not args.run:
        parser.error('--detach is only valid with --run')
    if args.plan:
        result = prepare(args)
        write_json(campaign / 'preflight.json', result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.preflight:
        print(json.dumps(preflight(campaign), ensure_ascii=False, indent=2))
    elif args.detach:
        preflight(campaign, require_idle=True)
        command = [sys.executable, str(Path(__file__).resolve()), '--campaign', args.campaign, '--run']
        print(json.dumps(launch_detached(command, campaign / 'runtime.json', cwd=ROOT), indent=2))
    else:
        execute(campaign)


if __name__ == '__main__':
    try:
        main()
    except RunInterrupted as error:
        sys.exit(128 + error.signum)
