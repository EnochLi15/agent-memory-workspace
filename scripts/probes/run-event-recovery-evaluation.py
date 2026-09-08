"""Frozen two-phase HTTP recovery: completed A06 QA, then B03/B30 original tails.

--plan/--validate are local only. Each campaign owns an immutable copy of this
entry point and its helper modules; later edits to reusable scripts do not alter
an already prepared campaign. Existing results are never reused.
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

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
if (ROOT / 'workspace-root.json').exists(): ROOT = Path(json.loads((ROOT / 'workspace-root.json').read_text())['root'])
sys.path.insert(0,str(HERE.parents[1]))
_spec = importlib.util.spec_from_file_location('event_qa_helpers', HERE.with_name('run-completed-sample-qa.py'))
qa = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(qa)
fixed, tail = qa.fixed, qa.tail
qa.ROOT = fixed.ROOT = tail.ROOT = ROOT
from experiment_runtime import ManagedRun, RunInterrupted, launch_detached, now, read_status, write_json
sha, read_json = tail.sha, tail.read_json
CAMPAIGN = 'priority-event-recovery-20260909-01'
CANDIDATE = ROOT / 'artifacts/priority-event-vocabulary-fix-20260909'
COMMIT = 'ce935bfd34e50e64e9fe5f149ceb3cc3b93ae9d6'
MANIFEST_SHA = '6697eb391c2528353d8b57173511584d6bd6ae8acbc9c635a09f248231b04160'
CASES = [('A06_forget', 51, 51, 5), ('B03_remember', 20, 50, 6), ('B30_forget', 13, 51, 5)]
ORIGINS = ['priority-full-20260908-01','priority-failed-tail-20260909-01','priority-failed-adds-20260909-01','priority-A06-history-qa-20260909-01']
SCOPE = 'Original mixed-version prefixes, event-vocabulary candidate tails and standard fixed questions. Phase denominators 5 and 11. Original failed campaigns and all earlier scores remain unchanged.'


def idle():
    states = {}
    for name in ORIGINS:
        state = read_status(ROOT / 'artifacts' / name / 'runtime.json')
        if state['status'] not in ('finished','failed','interrupted') or state.get('alive') or any(c.get('alive') for c in state.get('children',{}).values()):
            raise ValueError('Origin controller or owned child is not terminal: '+name)
        states[name] = state['status']
    return states


def prefix_receipts(path, schedule, namespace, sample, revision):
    user = namespace+':memops:'+sample
    expected = [r for r in schedule if r['sample_id']==sample][:revision]
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        meta = dict(db.execute('SELECT key,value FROM meta'))
        saved = {rid:{'hash':digest,'receipt':json.loads(receipt)} for rid,digest,receipt in db.execute('SELECT id,hash,receipt FROM requests')}
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok': raise ValueError('SQLite integrity failed')
    if meta.get('user_id')!=user or meta.get('source_format')!='dual-source-v5-s1' or int(meta.get('revision',-1))!=revision:
        raise ValueError('Tenant, source format or revision changed: '+sample)
    if len(expected)!=revision or len(saved)!=revision or set(saved)!={r['request_id'] for r in expected}: raise ValueError('Not the complete exact request prefix: '+sample)
    for row in expected:
        rid=row['request_id']; session=rid[len(user)+1:].rsplit(':',1)[0]
        if saved[rid]!={'hash':row['hash'],'receipt':{'success':True,'request_id':rid,'user_id':user,'session_id':session}}:
            raise ValueError('Original request hash or HTTP receipt changed: '+sample)
    return saved


def origin_inputs():
    idle()
    original,a06,a06_schedule,a06_saved,a06_db,files,dependencies=qa.origin_inputs()
    added=ROOT/'artifacts/priority-failed-adds-20260909-01'; add_plan=read_json(added/'plan.json')
    inputs,full,_,old_prefix=fixed.validate_inputs(added/'frozen-inputs.private.json',add_plan['inputs_sha256'])
    if add_plan['protocol']!='fixed-failed-adds-http-v1' or fixed.all_hashes(Path(add_plan['candidate_dist']))!=add_plan['candidate_files']:
        raise ValueError('Original successful-add execution artifact changed')
    if add_plan['candidate_provenance']['commit']!='50ccac83fa59f85de418dcde2c4e69444c99607b': raise ValueError('Unexpected successful-add baseline')
    risk=next(p for p in full['phases'] if p['id']=='memops-risk')
    all_samples=read_json(risk['data_file']); selected=[]
    for sample,_,_,questions in CASES:
        matches=[s for s in all_samples if s['sample_id']==sample]
        if len(matches)!=1 or matches[0]['benchmark']!='memops' or len(matches[0]['sessions'])!=50 or len(matches[0]['questions'])!=questions:
            raise ValueError('Full original sample changed: '+sample)
        selected.extend(matches)
    qids=[q['qid'] for s in selected for q in s['questions']]
    if len(set(qids))!=16: raise ValueError('Question IDs duplicate or denominator changed')
    schedule=tail.request_schedule(Path(original['eval_code_root']),Path(risk['data_file']),full['namespace'])
    schedule=[r for r in schedule if r['sample_id'] in {c[0] for c in CASES}]
    results=read_json(added/'results.private.json'); snapshots=[]; receipts={}
    for sample,revision,total,_ in CASES:
        own=[r for r in schedule if r['sample_id']==sample]
        if len(own)!=total: raise ValueError('Original evaluator add count changed')
        if sample=='A06_forget': database=a06_db
        else:
            case=next(c for c in inputs['cases'] if c['sample_id']==sample)
            matches=[r for r in results if r.get('request_id')==case['request_id']]
            if len(matches)!=1 or matches[0].get('http_status')!=200: raise ValueError('Missing actual successful fixed HTTP add')
            database=Path(add_plan['data_dir'])/sha(case['request']['user_id'].encode())/'memory.sqlite'
            index=inputs['cases'].index(case);raw_result=added/'http-results'/(str(index)+'.private.json')
            if read_json(raw_result)!=matches[0]:raise ValueError('Original standalone HTTP result differs from aggregate')
            files[str(raw_result)]=sha(raw_result.read_bytes())
            evidence=fixed.database_result(database,case,old_prefix,matches[0])
            if evidence['integrity']!='pass' or evidence['revision_after']!=revision: raise ValueError('Successful HTTP add and stored receipt differ')
        receipts.update(prefix_receipts(database,schedule,full['namespace'],sample,revision))
        snapshots.append({'sample_id':sample,'revision':revision,'source':str(database),'source_sha256':sha(database.read_bytes()),'logical_state':fixed.logical_state(database)})
    for path in [added/'plan.json',added/'frozen-inputs.private.json',added/'results.private.json',added/'service.json',Path(risk['data_file'])]: files[str(path)]=sha(path.read_bytes())
    return original,selected,schedule,receipts,snapshots,files,dependencies


def freeze_executor(campaign):
    executor=campaign/'executor'; probes=executor/'scripts/probes'; probes.mkdir(parents=True)
    for path in [HERE,Path(qa.__file__),Path(fixed.__file__),Path(tail.__file__)]: shutil.copy2(path,probes/path.name)
    runtime=Path(sys.modules['experiment_runtime'].__file__)
    shutil.copy2(runtime,executor/'scripts/experiment_runtime.py')
    write_json(executor/'workspace-root.json',{'root':str(ROOT)})
    return probes/HERE.name,{str(p):sha(p.read_bytes()) for p in executor.rglob('*') if p.is_file()}


def launcher(campaign):
    # Keep the original response and successful search-vector object unchanged.
    return qa.launcher(campaign,campaign/'service-dist').replace(
        "const expected=JSON.parse(readFileSync(RECEIPTS,'utf8')),app=await buildServer(config);".replace('RECEIPTS',json.dumps(str(campaign/'receipts.private.json'))),
        "const expected=JSON.parse(readFileSync("+json.dumps(str(campaign/'receipts.private.json'))+",'utf8')),schedule=Object.fromEntries(JSON.parse(readFileSync("+json.dumps(str(campaign/'schedule.json'))+",'utf8')).map(r=>[r.request_id,r.hash])),app=await buildServer(config);\napp.addHook('preHandler',async(request,reply)=>{if(request.routeOptions.url==='/add'){const h=createHash('sha256').update(JSON.stringify(request.body)).digest('hex');if(schedule[request.body?.request_id]!==h)return reply.code(409).send({error:{code:'RECOVERY_SCHEDULE_MISMATCH',message:'Request is outside the frozen recovery schedule'}});}});").replace(
        'JSON.stringify({request_id,hash,status:reply.statusCode,matches_snapshot:matches})',
        'JSON.stringify({request_id,hash,status:reply.statusCode,classification:known?"prefix":"tail",matches_schedule:schedule[request_id]===hash,matches_snapshot:known?matches:null})')


def fresh(campaign, phases, allow_owned_launch=False):
    tail.fresh_outputs(campaign,Path(phases[0]['run_dir']),allow_owned_launch)
    for phase in phases:
        if Path(phase['run_dir']).exists(): raise FileExistsError('Preserve existing phase output')
    for name in ['service.json','model-calls.jsonl','model-trace.jsonl','http-receipts.jsonl','question-status.json','phase-status.json']:
        if (campaign/name).exists(): raise FileExistsError('Preserve existing execution: '+name)


def prepare(args):
    campaign=ROOT/'artifacts'/tail.name(args.campaign)
    if campaign.exists(): raise FileExistsError('Choose a new campaign ID')
    original,samples,schedule,receipts,snapshots,inputs,dependencies=origin_inputs()
    manifest=read_json(args.candidate_manifest); hashes=fixed.all_hashes(args.candidate_dist)
    if sha(args.candidate_manifest.read_bytes())!=MANIFEST_SHA or manifest['candidate_commit']!=COMMIT or hashes!=manifest['dist_files'] or len(hashes)!=141:
        raise ValueError('Expected exact ce935 candidate and full frozen manifest')
    if not 1024<=args.port<=65535 or args.port in (8115,8116,8117,8118): raise ValueError('Use an independent valid port')
    campaign.mkdir(mode=0o700)
    phases=[]
    for id,chosen,timeout in [('a06',samples[:1],1800),('b03-b30',samples[1:],21600)]:
        data=campaign/(id+'-samples.json');write_json(data,chosen)
        run_id=campaign.name+'-candidate-memops-'+id
        phases.append({'id':id,'samples':[s['sample_id'] for s in chosen],'dataset':str(data),'dataset_sha256':sha(data.read_bytes()),'planned_questions':sum(len(s['questions']) for s in chosen),'run_id':run_id,'run_dir':str(ROOT/'eval/artifacts'/run_id),'timeout_seconds':timeout})
    fresh(campaign,phases)
    shutil.copytree(args.candidate_dist,campaign/'service-dist');shutil.copy2(args.candidate_manifest,campaign/'candidate-manifest.json')
    write_json(campaign/'schedule.json',schedule);write_json(campaign/'receipts.private.json',receipts)
    (campaign/'snapshots').mkdir()
    for snapshot in snapshots:
        target=campaign/'snapshots'/(snapshot['sample_id']+'.sqlite')
        with closing(sqlite3.connect(Path(snapshot['source']).resolve().as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(target)) as dst: src.backup(dst)
        if fixed.logical_state(target)!=snapshot['logical_state']: raise ValueError('Snapshot changed during capture')
        snapshot.update(path=str(target),sha256=sha(target.read_bytes()))
    (campaign/'node_modules').symlink_to(Path(original['eval_code_root']).parent/'service/node_modules',target_is_directory=True)
    write_json(campaign/'package.json',{'type':'module'})
    (campaign/'observe-search.mjs').write_text(qa.OBSERVER);(campaign/'service-launcher.mjs').write_text(launcher(campaign))
    entry,frozen=freeze_executor(campaign);dependencies.update(frozen)
    dependencies.update({str(campaign/f):sha((campaign/f).read_bytes()) for f in ['observe-search.mjs','service-launcher.mjs','package.json']})
    pinned={str(p):sha(p.read_bytes()) for p in [campaign/'candidate-manifest.json',campaign/'schedule.json',campaign/'receipts.private.json',*[Path(p['dataset']) for p in phases],*[Path(s['path']) for s in snapshots]]}
    plan={'protocol':'event-recovery-qa-v1','campaign':campaign.name,'created_at':now(),'scope':SCOPE,'entry_point':str(entry),
          'namespace':original['namespace'],'eval_code_root':original['eval_code_root'],'eval_commit':tail.EVAL_COMMIT,'spec':original['spec'],'env_file':original['env_file'],
          'candidate_commit':COMMIT,'candidate_dist':str(campaign/'service-dist'),'candidate_files':hashes,'candidate_manifest_sha256':MANIFEST_SHA,
          'origin_service_config':str(ROOT/'artifacts/priority-failed-tail-20260909-01/service.json'),
          'port':args.port,'data_dir':str(campaign/'data'),'phases':phases,'planned_questions':16,'prefix_receipts':84,'total_adds':152,'new_tail_adds':68,
          'snapshots':snapshots,'origin_files':inputs,'dependencies':dependencies,'pinned_files':pinned}
    write_json(campaign/'plan.json',plan)
    result=validate(campaign);write_json(campaign/'validation.json',result);return result


def validate_paths(campaign,plan):
    expected={'entry_point':str(campaign/'executor/scripts/probes'/HERE.name),'data_dir':str(campaign/'data'),
              'candidate_dist':str(campaign/'service-dist'),'origin_service_config':str(ROOT/'artifacts/priority-failed-tail-20260909-01/service.json')}
    if any(plan.get(k)!=v for k,v in expected.items()):raise ValueError('Frozen entry, service configuration or output paths changed')
    if type(plan.get('port')) is not int or not 1024<=plan['port']<=65535 or plan['port'] in (8115,8116,8117,8118):raise ValueError('Independent service port changed')
    if len(plan.get('phases',[]))!=2:raise ValueError('Exactly two ordered phases are required')
    for phase,id,samples,questions,timeout in zip(plan['phases'],['a06','b03-b30'],[['A06_forget'],['B03_remember','B30_forget']],[5,11],[1800,21600]):
        run_id=campaign.name+'-candidate-memops-'+id
        wanted={'id':id,'samples':samples,'planned_questions':questions,'dataset':str(campaign/(id+'-samples.json')),
                'run_id':run_id,'run_dir':str(ROOT/'eval/artifacts'/run_id),'timeout_seconds':timeout}
        if any(phase.get(k)!=v for k,v in wanted.items()):raise ValueError('Ordered phase identity, inputs, output or deadline changed')
    executor=campaign/'executor'
    needed=[executor/'scripts/probes'/name for name in [HERE.name,'run-completed-sample-qa.py','run-fixed-failed-adds.py','run-failed-tail-evaluation.py']]
    needed += [executor/'scripts/experiment_runtime.py',executor/'workspace-root.json']
    if any(str(p) not in plan['dependencies'] for p in needed):raise ValueError('Missing frozen execution dependency')
    if HERE==Path(plan['entry_point']):
        actual=[HERE,Path(qa.__file__).resolve(),Path(fixed.__file__).resolve(),Path(tail.__file__).resolve(),Path(sys.modules['experiment_runtime'].__file__).resolve()]
        if actual!=needed[:-1]:raise ValueError('Runtime imported a live helper outside the frozen executor')


def validate(campaign,allow_owned_launch=False):
    plan=read_json(campaign/'plan.json');validate_paths(campaign,plan);fresh(campaign,plan['phases'],allow_owned_launch)
    if plan['protocol']!='event-recovery-qa-v1' or plan['campaign']!=campaign.name or plan['candidate_commit']!=COMMIT or plan['candidate_manifest_sha256']!=MANIFEST_SHA:
        raise ValueError('Campaign identity changed')
    for path,h in {**plan['origin_files'],**plan['dependencies'],**plan['pinned_files']}.items():
        if sha(Path(path).read_bytes())!=h: raise ValueError('Frozen dependency or input changed: '+path)
    if fixed.all_hashes(Path(plan['candidate_dist']))!=plan['candidate_files'] or plan['candidate_files']!=read_json(campaign/'candidate-manifest.json')['dist_files']:
        raise ValueError('Candidate artifact changed')
    original,samples,schedule,receipts,snapshots,_,_=origin_inputs()
    if plan['namespace']!=original['namespace'] or plan['eval_commit']!=tail.EVAL_COMMIT or plan['eval_code_root']!=original['eval_code_root'] or plan['spec']!=original['spec'] or plan['env_file']!=original['env_file']:
        raise ValueError('Original evaluation identity changed')
    if (plan['planned_questions'],plan['prefix_receipts'],plan['total_adds'],plan['new_tail_adds'])!=(16,84,152,68): raise ValueError('Denominators or schedule counts changed')
    if read_json(campaign/'schedule.json')!=schedule or read_json(campaign/'receipts.private.json')!=receipts: raise ValueError('Original schedule or prefix changed')
    for phase,chosen in zip(plan['phases'],[samples[:1],samples[1:]]):
        if read_json(phase['dataset'])!=chosen or phase['planned_questions']!=sum(len(s['questions']) for s in chosen): raise ValueError('Phase input changed')
        actual=tail.request_schedule(Path(plan['eval_code_root']),Path(phase['dataset']),plan['namespace'])
        if actual!=[r for r in schedule if r['sample_id'] in phase['samples']]: raise ValueError('Frozen actual evaluator schedule differs')
    if len(plan['snapshots'])!=len(snapshots): raise ValueError('Snapshot partition changed')
    for frozen,current in zip(plan['snapshots'],snapshots):
        if any(frozen[k]!=v for k,v in current.items()) or sha(Path(frozen['path']).read_bytes())!=frozen['sha256'] or fixed.logical_state(frozen['path'])!=frozen['logical_state']:
            raise ValueError('Original or captured snapshot changed')
        prefix_receipts(frozen['path'],schedule,plan['namespace'],frozen['sample_id'],frozen['revision'])
    return {'validation':'passed','model_calls_made':0,'candidate_commit':COMMIT,'compiled_files':141,'planned_questions':16,'phase_denominators':[5,11],'prefix_receipts':84,'new_tail_adds':68,'total_http_adds':152,'origin_runtime_statuses':idle(),'entry_point':plan['entry_point']}


def complete_rows(path):
    if not Path(path).exists(): return [],False
    raw=Path(path).read_bytes();partial=bool(raw and not raw.endswith(b'\n'))
    lines=raw.splitlines();return [json.loads(line) for line in (lines[:-1] if partial else lines) if line.strip()],partial


def final_database(snapshot, schedule, namespace, successful, failed):
    sample=snapshot['sample_id'];user=namespace+':memops:'+sample
    original=Path(snapshot['source']);clone=Path(snapshot['clone'])
    reasons=[]
    if sha(original.read_bytes())!=snapshot['source_sha256'] or fixed.logical_state(original)!=snapshot['logical_state']: reasons.append('origin_database_changed')
    expected=[r for r in schedule if r['sample_id']==sample]
    prefix={r['request_id'] for r in expected[:snapshot['revision']]}
    tail_ok=successful & ({r['request_id'] for r in expected}-prefix)
    wanted=prefix|tail_ok
    with closing(sqlite3.connect(clone.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        meta=dict(db.execute('SELECT key,value FROM meta'))
        saved={rid:{'hash':digest,'receipt':json.loads(receipt)} for rid,digest,receipt in db.execute('SELECT id,hash,receipt FROM requests')}
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok': reasons.append('clone_sqlite_integrity')
    if meta.get('user_id')!=user or meta.get('source_format')!='dual-source-v5-s1': reasons.append('clone_tenant_or_format')
    if int(meta.get('revision',-1))!=snapshot['revision']+len(tail_ok): reasons.append('revision_success_receipt_increment_mismatch')
    if set(saved)!=wanted or set(saved)&failed: reasons.append('clone_receipt_set_or_failed_request_present')
    for row in expected:
        rid=row['request_id']
        if rid not in wanted:continue
        session=rid[len(user)+1:].rsplit(':',1)[0]
        receipt={'success':True,'request_id':rid,'user_id':user,'session_id':session}
        if saved.get(rid)!={'hash':row['hash'],'receipt':receipt}:reasons.append('prefix_or_success_receipt_changed')
    unchanged=fixed.logical_state(clone)==snapshot['logical_state']
    if sample=='A06_forget' and not unchanged:reasons.append('A06_clone_state_changed')
    return {'sample_id':sample,'integrity':'fail' if reasons else 'pass','reasons':reasons,'revision_before':snapshot['revision'],'revision_after':int(meta.get('revision',-1)),'new_success_receipts':len(tail_ok),'prefix_receipts':len(prefix),'clone_logical_state_unchanged':unchanged}


def terminal_ingest(records):
    grouped={};reasons=[]
    for row in records:grouped.setdefault(row['request_id'],[]).append(row)
    for rid,attempts in grouped.items():
        if len(attempts)>3 or [r.get('attempt') for r in attempts]!=list(range(len(attempts))):reasons.append('invalid_transport_attempt_sequence')
        if any(r.get('status')!='retrying' for r in attempts[:-1]) or attempts[-1].get('status') not in ('retrying','ok','failed'):reasons.append('multiple_or_invalid_terminal_ingest')
    return {rid:rows[-1] for rid,rows in grouped.items()},reasons


def phase_result(campaign,plan,phase):
    reasons=[];incomplete=[]
    def records(path):
        try:
            values,partial=complete_rows(path)
            if partial: incomplete.append('incomplete_jsonl:'+Path(path).name)
            return values
        except (ValueError,OSError): reasons.append('invalid_jsonl:'+Path(path).name);return []
    directory=Path(phase['run_dir']);judgments=records(directory/'judgments.jsonl');ingest=records(directory/'ingest.jsonl')
    all_schedule=read_json(campaign/'schedule.json');schedule=[r for r in all_schedule if r['sample_id'] in phase['samples']]; expected={r['request_id']:r for r in schedule}
    all_prefix=read_json(campaign/'receipts.private.json');prefix={rid:r for rid,r in all_prefix.items() if rid in expected}
    all_http=records(campaign/'http-receipts.jsonl');http=[r for r in all_http if r.get('request_id') in expected]
    if any(r.get('request_id') not in {x['request_id'] for x in all_schedule} for r in all_http):reasons.append('unknown_HTTP_request')
    latest,ingest_errors=terminal_ingest(ingest);reasons.extend(ingest_errors)
    attempt_counts=Counter(r['request_id'] for r in ingest)
    if any(n>attempt_counts[rid] or n>3 for rid,n in Counter(r['request_id'] for r in http).items()) or any(not r.get('matches_schedule') for r in http):reasons.append('HTTP_schedule_or_attempt_count_changed')
    if any(r['request_id'] in prefix and r['status']==200 and not r.get('matches_snapshot') for r in http):reasons.append('prefix_HTTP_receipt_changed')
    if {r['request_id'] for r in http}!=set(expected):incomplete.append('HTTP_schedule_not_completed')
    if set(latest)!=set(expected) or any(r.get('status')!='ok' for r in latest.values()):incomplete.append('ingest_incomplete_or_failed')
    if any(rid not in expected for rid in latest):reasons.append('ingest_unknown_request')
    questions=[q for s in read_json(phase['dataset']) for q in s['questions']];qids=[q['qid'] for q in questions]
    if Counter(r['qid'] for r in judgments)!=Counter(qids):incomplete.append('question_record_missing_or_duplicate')
    if any(n!=1 for n in Counter(r['qid'] for r in judgments).values()) or any(r['qid'] not in qids for r in judgments):reasons.append('question_unknown_or_duplicate')
    manifest=read_json(directory/'manifest.json') if (directory/'manifest.json').exists() else {}
    for key,value in {'status':'finished','run_id':phase['run_id'],'memory_namespace':plan['namespace'],'planned_questions':phase['planned_questions'],'dataset_sha256':phase['dataset_sha256'],'eval_commit':tail.EVAL_COMMIT,'answer_model':'glm-5.2','judge_model':'glm-5.2','judge_kind':'rubric','mode':'proxy'}.items():
        if manifest.get(key)!=value:incomplete.append('manifest_mismatch:'+key)
    traces={r.get('trace_id'):r.get('identity',{}).get('request_id') for r in records(campaign/'model-trace.jsonl')}
    calls=records(campaign/'model-calls.jsonl');writes=[r for r in calls if r.get('kind')=='generation' and r.get('purpose')!='rerank']
    allowed={r['request_id'] for r in all_schedule}
    if any(traces.get(r.get('trace_id')) in all_prefix or traces.get(r.get('trace_id')) not in allowed for r in writes):reasons.append('prefix_or_unknown_write_generation')
    if phase['id']=='a06' and (writes or any(r.get('kind')=='embedding' and r.get('action')=='add' for r in calls)):reasons.append('A06_unexpected_write_model_call')
    successful={rid for rid,r in latest.items() if r.get('status')=='ok'}
    failed={rid for rid,r in latest.items() if r.get('status')=='failed'}
    http_success={r['request_id'] for r in http if r['status']==200}
    if successful-http_success:reasons.append('terminal_ok_without_successful_HTTP_response')
    db_results=[]
    for snapshot in plan['snapshots']:
        if snapshot['sample_id'] not in phase['samples']:continue
        clone=Path(plan['data_dir'])/sha((plan['namespace']+':memops:'+snapshot['sample_id']).encode())/'memory.sqlite'
        try:db=final_database({**snapshot,'clone':str(clone)},schedule,plan['namespace'],successful,failed)
        except (OSError,ValueError,sqlite3.Error):db={'sample_id':snapshot['sample_id'],'integrity':'fail','reasons':['clone_or_source_unavailable']}
        db_results.append(db);reasons.extend(db['reasons'])
    judged={r['qid']:r for r in judgments};statuses=[{'qid':qid,'status':judged.get(qid,{}).get('status','not_completed'),'correct':judged.get(qid,{}).get('correct'),'error':judged.get(qid,{}).get('error')} for qid in qids]
    attempts={r['request_id']:r for r in http}
    request_states=[{'request_id':rid,'classification':'prefix' if rid in prefix else 'tail','status':latest.get(rid,{}).get('status','not_attempted' if rid not in attempts else 'HTTP_recorded_without_ingest'),'http_status':attempts.get(rid,{}).get('status'),'physical_http_responses':sum(r['request_id']==rid for r in http),'transport_attempts':attempt_counts[rid]} for rid in expected]
    degraded=set()
    if (campaign/'service.log').exists():
        for line in (campaign/'service.log').read_text().splitlines():
            try: row=json.loads(line)
            except ValueError: continue
            if row.get('event')=='degraded' and row.get('request_id') in successful:degraded.add(row['request_id'])
    correct=sum(r.get('status')=='judged' and r.get('correct') is True for r in judgments)
    return {'phase':phase['id'],'integrity':'fail' if reasons else 'pass','reasons':reasons,'execution_status':'incomplete_or_failed' if incomplete else 'completed','execution_reasons':incomplete,'planned_questions':phase['planned_questions'],'correct':correct,'accuracy_over_planned':correct/phase['planned_questions'],'question_statuses':statuses,'request_statuses':request_states,'database_results':db_results,'verified_prefix_HTTP_receipts':len({r['request_id'] for r in http if r.get('matches_snapshot') is True}),'physical_HTTP_responses':len(http),'transport_attempts':len(ingest),'unique_logical_adds_attempted':len(latest),'new_tail_successful_adds':len(successful-set(prefix)),'successful_degraded_adds':len(degraded),'successful_degraded_request_ids':sorted(degraded),'degradation_counter_scope':'Only successful committed writes emit these events; absent events do not certify failed preparations as non-degraded.','write_generation_calls':sum(traces.get(r.get('trace_id')) in expected for r in writes),'search_rerank_generation_calls':sum(r.get('kind')=='generation' and r.get('purpose')=='rerank' for r in calls),'at':now()}


def frozen_ok(plan):
    return all(sha(Path(p).read_bytes())==h for p,h in {**plan['origin_files'],**plan['dependencies'],**plan['pinned_files']}.items()) and fixed.all_hashes(Path(plan['candidate_dist']))==plan['candidate_files']


def execute(campaign):
    validate(campaign,True);plan=read_json(campaign/'plan.json');spec=read_json(plan['spec']);env=os.environ.copy()
    for line in Path(plan['env_file']).read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key,value=line.split('=',1);env[key.strip()]=value.strip().strip('"').strip("'")
    env.update(spec['defaults']);env.update(spec['profiles']['candidate']['environment'])
    for key in ('EVALUATOR_API_BASE','EVALUATOR_API_KEY','EVALUATOR_MODEL'):env.pop(key,None)
    config=read_json(plan['origin_service_config']);config.pop('llmKey',None)
    config.update(dataDir=plan['data_dir'],port=plan['port'],host='127.0.0.1',recovery_execution={'scope':SCOPE,'candidate_commit':COMMIT,'artifact':plan['candidate_dist']})
    env.update(MEMORY_MODEL_AUDIT=str(campaign/'model-calls.jsonl'),MEMORY_MODEL_TRACE=str(campaign/'model-trace.jsonl'),MEMORY_RETRIEVAL_AUDIT=str(campaign/'retrieval-stages.jsonl'),MEMORY_LLM_BASE_URL=config['llmBase'])
    env['SERVICE_CONFIG_JSON']=json.dumps(config,sort_keys=True,separators=(',',':'));env['SERVICE_CONFIG_SHA256']=sha(env['SERVICE_CONFIG_JSON'].encode())
    with socket.socket() as probe:probe.bind(('127.0.0.1',plan['port']))
    results=[];failure=None
    with ManagedRun(campaign/'runtime.json') as runtime:
        try:
            with ExitStack() as files:
                idle()
                for snapshot in plan['snapshots']:
                    target=Path(plan['data_dir'])/sha((plan['namespace']+':memops:'+snapshot['sample_id']).encode());target.mkdir(parents=True)
                    shutil.copy2(snapshot['path'],target/'memory.sqlite')
                    if sha((target/'memory.sqlite').read_bytes())!=snapshot['sha256']:raise ValueError('Restored clone hash differs')
                    prefix_receipts(target/'memory.sqlite',read_json(campaign/'schedule.json'),plan['namespace'],snapshot['sample_id'],snapshot['revision'])
                write_json(campaign/'service.json',config)
                write_json(campaign/'question-status.json',[{'qid':q['qid'],'status':'not_started','correct':None} for phase in plan['phases'] for sample in read_json(phase['dataset']) for q in sample['questions']])
                if sys.platform=='darwin':runtime.spawn('sleep-prevention',['/usr/bin/caffeinate','-i','-w',str(os.getpid())],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                runtime.spawn('service',['node',str(campaign/'service-launcher.mjs')],required=True,cwd=campaign,env=env,stdin=subprocess.DEVNULL,stdout=tail.private_log(files,campaign/'service.log'),stderr=subprocess.STDOUT)
                base='http://127.0.0.1:'+str(plan['port'])
                for _ in range(80):
                    runtime.poll()
                    try:
                        with urllib.request.urlopen(base+'/health',timeout=1) as response:
                            if response.status==200:break
                    except Exception:time.sleep(.25)
                else:raise RuntimeError('Owned service did not become ready')
                for phase in plan['phases']:
                    if not frozen_ok(plan):raise ValueError('Frozen inputs or execution changed before phase')
                    command=['node',str(Path(plan['eval_code_root'])/'dist/cli.js'),'run','--data',phase['dataset'],'--output',phase['run_dir'],'--run-id',phase['run_id'],'--memory-namespace',plan['namespace'],'--base-url',base,'--concurrency','1','--answer-model','glm-5.2','--judge-model','glm-5.2','--judge-kind','rubric','--mode','proxy','--chunk-messages','20','--chunk-words','2000']
                    job=runtime.spawn('eval-'+phase['id'],command,cwd=plan['eval_code_root'],env=env,stdin=subprocess.DEVNULL,stdout=tail.private_log(files,campaign/(phase['id']+'-eval.log')),stderr=subprocess.STDOUT)
                    deadline=time.monotonic()+phase['timeout_seconds']
                    while job.poll() is None:
                        runtime.poll()
                        if time.monotonic()>deadline:raise TimeoutError('Phase deadline exceeded: '+phase['id'])
                        time.sleep(.2)
                    runtime.poll();result=phase_result(campaign,plan,phase)
                    if not frozen_ok(plan):result['integrity']='fail';result['reasons'].append('frozen_inputs_or_execution_changed_at_gate')
                    results.append(result);write_json(campaign/'phase-status.json',results)
                    if job.returncode or result['integrity']!='pass' or result['execution_status']!='completed':raise RuntimeError('Phase integrity failed: '+phase['id'])
        except BaseException as error:failure=error
        finally:
            fixed.stop_owned(runtime)
            # Preserve the planned denominator even if service startup or a phase failed.
            final=[]
            for phase in plan['phases']:
                try: final.append(next((r for r in results if r['phase']==phase['id']),None) or phase_result(campaign,plan,phase))
                except Exception as error:final.append({'phase':phase['id'],'integrity':'fail','reasons':['summary_error:'+type(error).__name__],'execution_status':'incomplete_or_failed','request_statuses':[],'planned_questions':phase['planned_questions'],'correct':0,'question_statuses':[{'qid':q['qid'],'status':'not_completed','correct':None} for s in read_json(phase['dataset']) for q in s['questions']]})
            unchanged=frozen_ok(plan)
            for phase in final:
                if not unchanged:phase['integrity']='fail';phase['reasons'].append('frozen_inputs_or_execution_changed')
                definition=next(p for p in plan['phases'] if p['id']==phase['phase'])
                try:
                    logs,_=complete_rows(Path(definition['run_dir'])/'ingest.jsonl');latest,errors=terminal_ingest(logs)
                    ok={rid for rid,r in latest.items() if r.get('status')=='ok'};failed={rid for rid,r in latest.items() if r.get('status')=='failed'}
                    phase['database_results']=[]
                    for snapshot in plan['snapshots']:
                        if snapshot['sample_id'] not in definition['samples']:continue
                        clone=Path(plan['data_dir'])/sha((plan['namespace']+':memops:'+snapshot['sample_id']).encode())/'memory.sqlite'
                        db=final_database({**snapshot,'clone':str(clone)},read_json(campaign/'schedule.json'),plan['namespace'],ok,failed)
                        phase['database_results'].append(db)
                        if sha(Path(snapshot['path']).read_bytes())!=snapshot['sha256']:errors.append('captured_snapshot_changed')
                        errors.extend(db['reasons'])
                    if errors:phase['integrity']='fail';phase['reasons'].extend(errors)
                except Exception as error:phase['integrity']='fail';phase['reasons'].append('final_database_error:'+type(error).__name__)
            write_json(campaign/'phase-status.json',final)
            write_json(campaign/'question-status.json',[q for phase in final for q in phase['question_statuses']])
            write_json(campaign/'request-status.json',[r for phase in final for r in phase['request_statuses']])
            write_json(campaign/'summary.json',{'protocol':'event-recovery-qa-result-v1','scope':SCOPE,'planned_questions':16,'correct':sum(p['correct'] for p in final),'phases':final,'integrity':'pass' if all(p['integrity']=='pass' for p in final) else 'fail','execution_status':'completed' if not failure and all(p['execution_status']=='completed' for p in final) else 'incomplete_or_failed','failure_type':type(failure).__name__ if failure else None,'at':now()})
        if failure:raise failure


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--campaign',default=CAMPAIGN)
    actions=parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--plan',action='store_true');actions.add_argument('--validate',action='store_true');actions.add_argument('--run',action='store_true')
    parser.add_argument('--candidate-dist',type=Path,default=CANDIDATE/'candidate-dist');parser.add_argument('--candidate-manifest',type=Path,default=CANDIDATE/'candidate-manifest.json')
    parser.add_argument('--port',type=int,default=8119);parser.add_argument('--detach',action='store_true');args=parser.parse_args();os.umask(0o077)
    campaign=ROOT/'artifacts'/tail.name(args.campaign)
    if args.detach and not args.run:parser.error('--detach requires --run')
    if args.plan:print(json.dumps(prepare(args),indent=2));return
    plan=read_json(campaign/'plan.json');entry=Path(plan['entry_point'])
    if HERE!=entry:
        os.execv(sys.executable,[sys.executable,str(entry),'--campaign',args.campaign,'--run' if args.run else '--validate',*(['--detach'] if args.detach else [])])
    if args.validate:print(json.dumps(validate(campaign),indent=2))
    elif args.detach:
        validate(campaign);print(json.dumps(launch_detached([sys.executable,str(entry),'--campaign',args.campaign,'--run'],campaign/'runtime.json',cwd=ROOT),indent=2))
    else:execute(campaign)


if __name__=='__main__':
    try:main()
    except RunInterrupted as error:sys.exit(128+error.signum)
