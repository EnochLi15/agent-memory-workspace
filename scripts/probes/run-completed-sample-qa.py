"""Evaluate exactly the completed A06 history from a partially failed campaign.

Planning/validation are local only. The frozen normal evaluator replays all 51
HTTP adds against an exact receipt clone, then answers/judges the original five
questions. Sample completeness never certifies the origin campaign as successful.
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
_spec = importlib.util.spec_from_file_location('completed_qa_fixed_helpers', Path(__file__).with_name('run-fixed-failed-adds.py'))
fixed = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(fixed)
tail = fixed.tail
from experiment_runtime import ManagedRun, RunInterrupted, launch_detached, now, read_status, write_json
sha, read_json = tail.sha, tail.read_json
SAMPLE, QUESTIONS, SESSIONS, ADDS = 'A06_forget', 5, 50, 51
ORIGIN, CAMPAIGN = 'priority-failed-tail-20260909-01', 'priority-A06-history-qa-20260909-01'
COMMIT = '8cc94c9b66a5228ac21c49ce471f9721220aee58'
CANDIDATE = ROOT / 'artifacts/priority-history-rendering-fix-20260909'
SCOPE = 'A06 five-question QA on its completed mixed-version frozen history; HTTP receipt replay only. The origin campaign remains partially failed. No fresh ingestion, broader score, changed questions or changed namespace.'


def rows(path):
    path = Path(path)
    if not path.exists(): return []
    raw = path.read_bytes()
    if raw and not raw.endswith(b'\n'): raise ValueError('Incomplete JSONL record: ' + str(path))
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def select_sample(data):
    chosen = [s for s in data if s['sample_id'] == SAMPLE]
    if len(chosen) != 1: raise ValueError('Exactly one complete A06 sample is required')
    sample = chosen[0]
    if sample['benchmark'] != 'memops' or len(sample['sessions']) != SESSIONS or len(sample['questions']) != QUESTIONS:
        raise ValueError('A06 full sessions/questions must remain unchanged')
    if len({q['qid'] for q in sample['questions']}) != QUESTIONS: raise ValueError('Duplicate A06 qid')
    return chosen


def complete_ingest(records, schedule):
    expected = [r['request_id'] for r in schedule]
    selected = [r for r in records if r.get('sample_id') == SAMPLE]
    if len(expected) != ADDS or len(set(expected)) != ADDS or len(selected) != ADDS:
        raise ValueError('A06 must have all 51 unique original HTTP adds')
    if Counter(r['request_id'] for r in selected) != Counter(expected) or any(r.get('status') != 'ok' for r in selected):
        raise ValueError('A06 ingestion is not complete and successful')


def receipts(path, schedule, namespace):
    user = namespace + ':memops:' + SAMPLE
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        meta = dict(db.execute('SELECT key,value FROM meta'))
        saved = {rid: {'hash': digest, 'receipt': json.loads(receipt)} for rid, digest, receipt in db.execute('SELECT id,hash,receipt FROM requests')}
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok': raise ValueError('SQLite integrity failed')
    if meta.get('user_id') != user or int(meta.get('revision', -1)) != ADDS or meta.get('source_format') != 'dual-source-v5-s1':
        raise ValueError('Completed A06 tenant, revision or source format differs')
    if len(schedule) != ADDS or len(saved) != ADDS or set(saved) != {r['request_id'] for r in schedule}:
        raise ValueError('Completed A06 exact receipt set differs')
    for r in schedule:
        rid = r['request_id']; session = rid[len(user)+1:].rsplit(':', 1)[0]
        wanted = {'success': True, 'request_id': rid, 'user_id': user, 'session_id': session}
        if saved[rid] != {'hash': r['hash'], 'receipt': wanted}: raise ValueError('Original HTTP payload hash or receipt differs')
    return saved


def idle(require=True):
    states, blockers = {}, []
    for name in ['priority-full-20260908-01', ORIGIN, 'priority-failed-adds-20260909-01']:
        path = ROOT / 'artifacts' / name / 'runtime.json'
        if not path.exists(): blockers.append(name + ':missing_ownership'); continue
        state = read_status(path); states[name] = state['status']
        if state['status'] not in ('finished', 'failed', 'interrupted') or state.get('alive') or any(c.get('alive') for c in state.get('children', {}).values()):
            blockers.append(name + ':controller_or_owned_child_not_stopped')
    if blockers and require: raise ValueError('Execution blocked: ' + ', '.join(blockers))
    return {'origin_runtime_statuses': states, 'blockers': blockers, 'cloud_start_allowed': not blockers}


def origin_inputs():
    idle()
    origin = ROOT / 'artifacts' / ORIGIN; plan = read_json(origin / 'plan.json')
    if plan['protocol'] != 'failed-tail-http-recovery-v1' or plan['eval_commit'] != tail.EVAL_COMMIT:
        raise ValueError('Unexpected original recovery/evaluator')
    full, original_samples = tail.pinned_origin(Path(plan['origin']))
    if sha((Path(plan['origin']) / 'plan.json').read_bytes()) != plan['origin_plan_sha256']:
        raise ValueError('Original full campaign plan changed')
    data = Path(plan['dataset']); manifest_file = Path(plan['run_dir']) / 'manifest.json'; manifest = read_json(manifest_file)
    service = read_json(origin / 'service.json')
    if sha(data.read_bytes()) != plan['dataset_sha256'] or select_sample(read_json(data)) != select_sample(original_samples):
        raise ValueError('Original complete A06 sample differs from frozen risk input')
    if service != manifest['service_configuration'] or service['dataDir'] != plan['data_dir']:
        raise ValueError('Original executed service configuration differs')
    expected = {'status': 'finished', 'run_id': plan['run_id'], 'dataset_sha256': plan['dataset_sha256'], 'memory_namespace': plan['namespace'],
                'eval_commit': tail.EVAL_COMMIT, 'mode': 'proxy', 'judge_kind': 'rubric', 'chunk_messages': 20, 'chunk_words': 2000,
                'answer_model': 'glm-5.2', 'judge_model': 'glm-5.2'}
    if any(manifest.get(k) != v for k, v in expected.items()): raise ValueError('Original evaluator configuration differs')
    if tail.dist_hashes(Path(plan['candidate_dist'])) != plan['dist_hashes']:
        raise ValueError('Original executed recovery service differs from its pinned JS hashes')
    if plan['namespace'] != full['namespace'] or plan['spec_sha256'] != full['spec_sha256'] or Path(plan['spec']) != Path(full['spec']):
        raise ValueError('Original namespace or spec differs')
    schedule = [r for r in tail.request_schedule(Path(plan['eval_code_root']), data, plan['namespace']) if r['sample_id'] == SAMPLE]
    complete_ingest(rows(Path(plan['run_dir']) / 'ingest.jsonl'), schedule)
    database = Path(service['dataDir']) / sha((plan['namespace'] + ':memops:' + SAMPLE).encode()) / 'memory.sqlite'
    saved = receipts(database, schedule, plan['namespace'])
    inputs = [origin / 'plan.json', origin / 'service.json', data, manifest_file, Path(plan['run_dir']) / 'ingest.jsonl', Path(plan['spec'])]
    files = {str(p): sha(p.read_bytes()) for p in inputs}
    dependencies = {str(Path(full['code_root']) / p): h for p, h in full['frozen_code_hashes'].items()}
    eval_root = Path(plan['eval_code_root'])
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=eval_root, text=True).strip() != tail.EVAL_COMMIT:
        raise ValueError('Executed evaluator checkout changed')
    for directory in [eval_root / 'dist', eval_root / 'python']:
        dependencies.update({str(p): sha(p.read_bytes()) for p in directory.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'})
    return plan, select_sample(read_json(data)), schedule, saved, database, files, dependencies


def fresh(campaign, evaluation, allow_owned_launch=False):
    tail.fresh_outputs(campaign, evaluation, allow_owned_launch)
    for filename in ['service.json', 'model-calls.jsonl', 'model-trace.jsonl', 'retrieval-stages.jsonl', 'http-receipts.jsonl', 'search-vectors.private.jsonl', 'eval.log']:
        if (campaign / filename).exists(): raise FileExistsError('Preserve existing execution: ' + filename)


OBSERVER = """export function observeSearch(Models,record,metadata,onError){
const original=Models.prototype.embedBatch;
Models.prototype.embedBatch=async function(...args){
const vectors=await original.apply(this,args);
if(args[1]==='search'){try{record({at:new Date().toISOString(),...metadata,texts:args[0],vectors});}catch(error){onError(error);}}
return vectors;
};}
"""


def launcher(campaign, dist):
    return """import {readFileSync,appendFileSync} from 'node:fs';
import {createHash} from 'node:crypto';import {isDeepStrictEqual} from 'node:util';
import {Models} from MODELS;import {buildServer} from SERVER;import {observeSearch} from './observe-search.mjs';
const config=JSON.parse(process.env.SERVICE_CONFIG_JSON);config.llmKey=process.env.MEMORY_LLM_API_KEY;
observeSearch(Models,row=>appendFileSync(VECTORS,JSON.stringify(row)+'\\n',{mode:0o600}),
{model:config.embeddingModel,digest:config.embeddingDigest,dimensions:config.embeddingDimensions,space:config.embeddingSpace},
()=>console.error(JSON.stringify({event:'search_vector_observer_error'})));
const expected=JSON.parse(readFileSync(RECEIPTS,'utf8')),app=await buildServer(config);
app.addHook('onSend',(request,reply,payload,done)=>{
if(request.routeOptions.url==='/add'){
const request_id=request.body?.request_id,known=Object.hasOwn(expected,request_id);
const hash=createHash('sha256').update(JSON.stringify(request.body)).digest('hex');let matches=false;
if(known){try{matches=reply.statusCode===200&&hash===expected[request_id].hash&&isDeepStrictEqual(JSON.parse(String(payload)),expected[request_id].receipt);}catch{}}
appendFileSync(AUDIT,JSON.stringify({request_id,hash,status:reply.statusCode,matches_snapshot:matches})+'\\n',{mode:0o600});
}done(null,payload);});
await app.listen({host:'127.0.0.1',port:config.port});
for(const signal of ['SIGINT','SIGTERM'])process.once(signal,()=>{void app.close();});
""".replace('MODELS', json.dumps((dist / 'models.js').as_uri())).replace('SERVER', json.dumps((dist / 'server.js').as_uri())).replace('VECTORS', json.dumps(str(campaign / 'search-vectors.private.jsonl'))).replace('RECEIPTS', json.dumps(str(campaign / 'receipts.private.json'))).replace('AUDIT', json.dumps(str(campaign / 'http-receipts.jsonl')))


def prepare(args):
    campaign = ROOT / 'artifacts' / tail.name(args.campaign)
    if campaign.exists(): raise FileExistsError('Preserve existing campaign; choose a new ID')
    original, samples, schedule, saved, database, inputs, dependencies = origin_inputs()
    dist, hashes, provenance = fixed.candidate(args.candidate_dist, args.candidate_manifest, COMMIT)
    baseline = fixed.all_hashes(Path(original['candidate_dist']))
    if len(hashes) != 141 or set(hashes) != set(baseline) or {k for k in hashes if hashes[k] != baseline[k]} != {'retrieval.js', 'retrieval.js.map'}:
        raise ValueError('QA candidate must differ only in the frozen retrieval rendering artifacts')
    if not 1024 <= args.port <= 65535 or args.port in [8115, 8116, 8117]: raise ValueError('Use an independent valid port')
    evaluation = ROOT / 'eval/artifacts' / (campaign.name + '-candidate-memops')
    fresh(campaign, evaluation); campaign.mkdir(mode=0o700)
    shutil.copytree(dist, campaign / 'service-dist'); shutil.copy2(args.candidate_manifest, campaign / 'candidate-manifest.json')
    write_json(campaign / 'samples.json', samples); write_json(campaign / 'schedule.json', schedule); write_json(campaign / 'receipts.private.json', saved)
    snapshot = campaign / 'A06.sqlite'
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as src, closing(sqlite3.connect(snapshot)) as dest: src.backup(dest)
    logical = fixed.logical_state(database)
    if fixed.logical_state(snapshot) != logical or receipts(snapshot, schedule, original['namespace']) != saved:
        raise ValueError('A06 snapshot changed during capture')
    (campaign / 'node_modules').symlink_to(Path(original['eval_code_root']).parent / 'service/node_modules', target_is_directory=True)
    write_json(campaign / 'package.json', {'type': 'module'})
    (campaign / 'observe-search.mjs').write_text(OBSERVER)
    (campaign / 'service-launcher.mjs').write_text(launcher(campaign, campaign / 'service-dist'))
    dependencies.update({str(p): sha(p.read_bytes()) for p in [Path(__file__).resolve(), Path(fixed.__file__), Path(tail.__file__), ROOT / 'scripts/experiment_runtime.py', campaign / 'observe-search.mjs', campaign / 'service-launcher.mjs', campaign / 'package.json']})
    pinned = {filename: sha((campaign / filename).read_bytes()) for filename in ['samples.json', 'schedule.json', 'receipts.private.json', 'candidate-manifest.json', 'A06.sqlite']}
    plan = {'protocol': 'completed-sample-qa-v1', 'campaign': campaign.name, 'created_at': now(), 'scope': SCOPE,
            'origin': str(ROOT / 'artifacts' / ORIGIN), 'origin_files': inputs, 'dependencies': dependencies,
            'namespace': original['namespace'], 'planned_questions': QUESTIONS, 'expected_adds': ADDS, 'sample': SAMPLE,
            'eval_code_root': original['eval_code_root'], 'eval_commit': tail.EVAL_COMMIT, 'spec': original['spec'], 'env_file': original['env_file'],
            'dataset': str(campaign / 'samples.json'), 'run_id': evaluation.name, 'run_dir': str(evaluation), 'port': args.port,
            'data_dir': str(campaign / 'data'), 'candidate_dist': str(campaign / 'service-dist'), 'candidate_files': hashes,
            'candidate_provenance': provenance, 'pinned_files': pinned, 'origin_database': str(database),
            'origin_database_sha256': sha(database.read_bytes()), 'database_logical_state': logical,
            'observer_scope': 'Read-only embedBatch observer: one original call, unchanged arguments and same returned vector object; only successful search vectors recorded locally.'}
    write_json(campaign / 'plan.json', plan)
    result = validate(campaign); write_json(campaign / 'validation.json', result); return result


def validate(campaign, allow_owned_launch=False):
    plan = read_json(campaign / 'plan.json')
    wanted = {'origin': str(ROOT / 'artifacts' / ORIGIN), 'data_dir': str(campaign / 'data'),
              'candidate_dist': str(campaign / 'service-dist'), 'dataset': str(campaign / 'samples.json'),
              'run_id': campaign.name + '-candidate-memops',
              'run_dir': str(ROOT / 'eval/artifacts' / (campaign.name + '-candidate-memops')), 'eval_commit': tail.EVAL_COMMIT}
    if any(plan.get(k) != v for k, v in wanted.items()): raise ValueError('QA execution paths or evaluator identity changed')
    fresh(campaign, Path(plan['run_dir']), allow_owned_launch)
    if plan['protocol'] != 'completed-sample-qa-v1' or plan['campaign'] != campaign.name or plan['planned_questions'] != QUESTIONS or plan['expected_adds'] != ADDS or plan['sample'] != SAMPLE:
        raise ValueError('QA identity or denominator changed')
    for p, h in {**plan['origin_files'], **plan['dependencies']}.items():
        if sha(Path(p).read_bytes()) != h: raise ValueError('Frozen input/execution changed: ' + p)
    for f, h in plan['pinned_files'].items():
        if sha((campaign / f).read_bytes()) != h: raise ValueError('Pinned artifact changed: ' + f)
    original, samples, schedule, saved, database, inputs, dependencies = origin_inputs()
    if plan['namespace'] != original['namespace'] or plan['eval_code_root'] != original['eval_code_root'] or plan['spec'] != original['spec'] or plan['env_file'] != original['env_file']:
        raise ValueError('Original namespace/evaluator/model configuration changed')
    if read_json(plan['dataset']) != samples or read_json(campaign / 'schedule.json') != schedule or read_json(campaign / 'receipts.private.json') != saved:
        raise ValueError('A06 history, questions, schedule or receipts changed')
    if tail.request_schedule(Path(plan['eval_code_root']), Path(plan['dataset']), plan['namespace']) != schedule:
        raise ValueError('Frozen evaluator schedule differs for selected A06')
    _, hashes, provenance = fixed.candidate(plan['candidate_dist'], campaign / 'candidate-manifest.json', COMMIT)
    if hashes != plan['candidate_files'] or provenance != plan['candidate_provenance']: raise ValueError('Candidate changed')
    if Path(plan['origin_database']) != database or sha(database.read_bytes()) != plan['origin_database_sha256'] or fixed.logical_state(database) != plan['database_logical_state'] or fixed.logical_state(campaign / 'A06.sqlite') != plan['database_logical_state']:
        raise ValueError('Original or cloned A06 database changed')
    return {'protocol': plan['protocol'], 'validation': 'passed', 'scope': SCOPE, 'sample': SAMPLE,
            'planned_questions': QUESTIONS, 'sessions': SESSIONS, 'verified_http_receipts': ADDS,
            'new_writes_expected': 0, 'candidate_commit': COMMIT, 'compiled_files': len(hashes), 'model_calls_made': 0, **idle()}


def result_summary(campaign, plan):
    directory = Path(plan['run_dir']); judgments = rows(directory / 'judgments.jsonl'); reasons = []
    expected = read_json(campaign / 'receipts.private.json'); http = rows(campaign / 'http-receipts.jsonl')
    if Counter(r['request_id'] for r in http) != Counter(expected.keys()) or any(not r.get('matches_snapshot') for r in http): reasons.append('HTTP_receipts_missing_changed_or_repeated')
    try: complete_ingest(rows(directory / 'ingest.jsonl'), read_json(campaign / 'schedule.json'))
    except ValueError: reasons.append('ingest_incomplete_or_failed')
    qids = [q['qid'] for s in read_json(plan['dataset']) for q in s['questions']]
    if Counter(r['qid'] for r in judgments) != Counter(qids): reasons.append('question_records_missing_or_duplicated')
    manifest = read_json(directory / 'manifest.json') if (directory / 'manifest.json').exists() else {}
    for key, value in {'status':'finished', 'run_id':plan['run_id'], 'memory_namespace':plan['namespace'], 'planned_questions':QUESTIONS,
                       'dataset_sha256':plan['pinned_files']['samples.json'], 'eval_commit':tail.EVAL_COMMIT,
                       'answer_model':'glm-5.2', 'judge_model':'glm-5.2', 'judge_kind':'rubric', 'mode':'proxy'}.items():
        if manifest.get(key) != value: reasons.append('manifest_mismatch:' + key)
    calls = rows(campaign / 'model-calls.jsonl'); write_calls = [r for r in calls if r.get('kind') == 'generation' and r.get('purpose') != 'rerank']
    add_embeddings = [r for r in calls if r.get('kind') == 'embedding' and r.get('action') == 'add']
    if write_calls or add_embeddings: reasons.append('unexpected_write_model_calls')
    clone = Path(plan['data_dir']) / sha((plan['namespace'] + ':memops:' + SAMPLE).encode()) / 'memory.sqlite'
    clone_unchanged = clone.exists() and fixed.logical_state(clone) == plan['database_logical_state']
    source_unchanged = sha(Path(plan['origin_database']).read_bytes()) == plan['origin_database_sha256'] and fixed.logical_state(plan['origin_database']) == plan['database_logical_state']
    if not clone_unchanged or not source_unchanged: reasons.append('database_logical_state_changed')
    if clone.exists() and receipts(clone, read_json(campaign / 'schedule.json'), plan['namespace']) != expected: reasons.append('final_receipts_changed')
    if any(sha(Path(p).read_bytes()) != h for p, h in {**plan['origin_files'], **plan['dependencies']}.items()) or fixed.all_hashes(Path(plan['candidate_dist'])) != plan['candidate_files']: reasons.append('frozen_input_or_execution_changed')
    correct = sum(r.get('status') == 'judged' and r.get('correct') is True for r in judgments)
    return {'protocol':'completed-sample-qa-result-v1', 'scope':SCOPE, 'integrity':'fail' if reasons else 'pass', 'reasons':reasons,
            'planned_questions':QUESTIONS, 'correct':correct, 'accuracy_over_planned':correct/QUESTIONS,
            'question_status_counts':dict(Counter(r.get('status') for r in judgments)), 'write_generation_calls':len(write_calls),
            'search_rerank_generation_calls':sum(r.get('kind') == 'generation' and r.get('purpose') == 'rerank' for r in calls),
            'add_embedding_calls':len(add_embeddings), 'verified_HTTP_receipts':sum(r.get('matches_snapshot') is True for r in http),
            'original_database_unchanged':source_unchanged, 'clone_logical_state_unchanged':clone_unchanged,
            'captured_search_vector_batches':len(rows(campaign / 'search-vectors.private.jsonl')), 'at':now()}


def execute(campaign):
    validate(campaign, allow_owned_launch=True); plan = read_json(campaign / 'plan.json'); spec = read_json(plan['spec'])
    env = os.environ.copy()
    for line in Path(plan['env_file']).read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1); env[key.strip()] = value.strip().strip('"').strip("'")
    env.update(spec['defaults']); env.update(spec['profiles']['candidate']['environment'])
    for key in ('EVALUATOR_API_BASE', 'EVALUATOR_API_KEY', 'EVALUATOR_MODEL'): env.pop(key, None)
    config = read_json(Path(plan['origin']) / 'service.json'); config.pop('llmKey', None)
    config.update(dataDir=plan['data_dir'],port=plan['port'],host='127.0.0.1',completed_sample_qa={'scope':SCOPE,'candidate':plan['candidate_provenance'],'artifact':plan['candidate_dist'],'origin':plan['origin']})
    env.update(MEMORY_MODEL_AUDIT=str(campaign/'model-calls.jsonl'),MEMORY_MODEL_TRACE=str(campaign/'model-trace.jsonl'),MEMORY_RETRIEVAL_AUDIT=str(campaign/'retrieval-stages.jsonl'),MEMORY_LLM_BASE_URL=config['llmBase'])
    env['SERVICE_CONFIG_JSON'] = json.dumps(config,sort_keys=True,separators=(',',':')); env['SERVICE_CONFIG_SHA256'] = sha(env['SERVICE_CONFIG_JSON'].encode())
    with socket.socket() as probe: probe.bind(('127.0.0.1', plan['port']))
    with ManagedRun(campaign / 'runtime.json') as runtime:
        failure = None
        try:
            with ExitStack() as files:
                idle(); target = Path(plan['data_dir']) / sha((plan['namespace'] + ':memops:' + SAMPLE).encode()); target.mkdir(parents=True)
                shutil.copy2(campaign / 'A06.sqlite', target / 'memory.sqlite'); write_json(campaign/'service.json',config)
                if sys.platform == 'darwin': runtime.spawn('sleep-prevention',['/usr/bin/caffeinate','-i','-w',str(os.getpid())],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                runtime.spawn('service',['node',str(campaign/'service-launcher.mjs')],required=True,cwd=campaign,env=env,stdin=subprocess.DEVNULL,stdout=tail.private_log(files,campaign/'service.log'),stderr=subprocess.STDOUT)
                base = f'http://127.0.0.1:{plan["port"]}'
                for _ in range(80):
                    runtime.poll()
                    try:
                        with urllib.request.urlopen(base+'/health',timeout=1) as response:
                            if response.status == 200: break
                    except Exception: time.sleep(.25)
                else: raise RuntimeError('Owned QA service did not become ready')
                command=['node',str(Path(plan['eval_code_root'])/'dist/cli.js'),'run','--data',plan['dataset'],'--output',plan['run_dir'],'--run-id',plan['run_id'],'--memory-namespace',plan['namespace'],'--base-url',base,'--concurrency','1','--answer-model','glm-5.2','--judge-model','glm-5.2','--judge-kind','rubric','--mode','proxy','--chunk-messages','20','--chunk-words','2000']
                job=runtime.spawn('a06-qa',command,cwd=plan['eval_code_root'],env=env,stdin=subprocess.DEVNULL,stdout=tail.private_log(files,campaign/'eval.log'),stderr=subprocess.STDOUT)
                while job.poll() is None: runtime.poll(); time.sleep(.2)
                runtime.poll()
                if job.returncode: raise RuntimeError('Frozen evaluator failed; preserve all question errors')
        except BaseException as error: failure = error
        finally: fixed.stop_owned(runtime)
        summary=result_summary(campaign,plan);write_json(campaign/'summary.json',summary)
        if failure is not None: raise failure
        if summary['integrity'] != 'pass': raise RuntimeError('QA integrity validation failed; preserve results')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--campaign',default=CAMPAIGN)
    actions=parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--plan',action='store_true');actions.add_argument('--validate',action='store_true');actions.add_argument('--run',action='store_true')
    parser.add_argument('--candidate-dist',type=Path,default=CANDIDATE/'candidate-dist');parser.add_argument('--candidate-manifest',type=Path,default=CANDIDATE/'candidate-manifest.json')
    parser.add_argument('--port',type=int,default=8118);parser.add_argument('--detach',action='store_true');args=parser.parse_args();os.umask(0o077)
    campaign=ROOT/'artifacts'/tail.name(args.campaign)
    if args.detach and not args.run: parser.error('--detach requires --run')
    if args.plan: print(json.dumps(prepare(args),indent=2))
    elif args.validate: print(json.dumps(validate(campaign),indent=2))
    elif args.detach:
        validate(campaign);print(json.dumps(launch_detached([sys.executable,str(Path(__file__).resolve()),'--campaign',args.campaign,'--run'],campaign/'runtime.json',cwd=ROOT),indent=2))
    else: execute(campaign)


if __name__ == '__main__':
    try: main()
    except RunInterrupted as error: sys.exit(128+error.signum)
