"""Fixed 16-question QA over three completed databases; planning never calls models."""
import argparse, importlib.util, json, os, shutil, sqlite3, sys
from collections import Counter
from contextlib import closing
from pathlib import Path

HERE=Path(__file__).resolve();ROOT=HERE.parents[2]
FROZEN=(ROOT/'workspace-root.json').exists()
if FROZEN:ROOT=Path(json.loads((ROOT/'workspace-root.json').read_text())['root'])
BASE_CORE=ROOT/'artifacts/priority-event-recovery-20260909-01/executor/scripts/probes/run-event-recovery-evaluation.py'
CORE=HERE.with_name(BASE_CORE.name) if FROZEN else BASE_CORE
sys.path.insert(0,str(CORE.parents[1]))
spec=importlib.util.spec_from_file_location('focus_qa_core',CORE);core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)
fixed,tail=core.fixed,core.tail
sha,read,write=core.sha,core.read_json,core.write_json
CORE_SHA='a632fce489fb21428b1db03c08411e3eded8168ccd48eb9e5ea0a0112c3389bf'
BUNDLE=ROOT/'artifacts/priority-query-focus-paired-20260909/inputs.private.json'
BUNDLE_SHA='086912dab377a86d0cbdf37a5542c756367b429837cde7b76c2f30d1c5b61161'
CANDIDATE=ROOT/'artifacts/priority-query-focus-20260909'
COMMIT='9c7cf1bc3daa77288202c68dcba321686e478aaa'
MANIFEST_SHA='70a4b158a4e6e25b6db211c820574e6909e1ef67987f4cff2b8d0726fa4a7efe'
CAMPAIGN='priority-query-focus-qa-20260909-01'
CASES=[('B03_remember',50,6,'single-B03-tail-qa-v1'),('B30_forget',51,5,'single-B30-tail-qa-v1'),('A06_forget',51,5,'event-recovery-qa-v1')]
SCOPE='One fresh Engine, 152 exact idempotent HTTP adds and 16 original standard questions over completed B03/B30/A06 snapshots. Query focus enabled; prior classifier samples and scores are not reused.'
OVERRIDE={'queryFocus':True,'queryFocusTimeout':8000}


def adapt_core(raw):
    if sha(raw)!=CORE_SHA:raise ValueError('Frozen recovery core differs from its reviewed hash')
    text=raw.decode()
    for old,new in [("r.get('purpose')!='rerank'","r.get('purpose') not in ('rerank','query_focus')"),
                    ("if sample=='A06_forget' and not unchanged:reasons.append('A06_clone_state_changed')","if not unchanged:reasons.append('completed_clone_state_changed')")]:
        if text.count(old)!=1:raise ValueError('Expected exactly one reviewed core replacement')
        text=text.replace(old,new)
    return text


def idle():
    states={}
    for source in read(BUNDLE)['sources']:
        path=Path(source['campaign']);state=core.read_status(path/'runtime.json')
        if state['status'] not in ('finished','failed','interrupted') or state.get('alive') or any(c.get('alive') for c in state.get('children',{}).values()):raise ValueError('Origin still owns a live process')
        states[path.name]=state['status']
    return states


def partition(samples,schedule,receipts):
    if len(samples)!=3 or len(schedule)!=152 or len(receipts)!=152 or len({r['request_id'] for r in schedule})!=152:raise ValueError('Exactly 152 complete prefix requests are required')
    for sample,(name,total,questions,_) in zip(samples,CASES):
        if sample['sample_id']!=name or sample['benchmark']!='memops' or len(sample['sessions'])!=50 or len(sample['questions'])!=questions or sum(r['sample_id']==name for r in schedule)!=total:raise ValueError('Fixed sample partition changed')
    if len({q['qid'] for s in samples for q in s['questions']})!=16 or set(receipts)!={r['request_id'] for r in schedule}:raise ValueError('Fixed 16 questions or complete receipt set changed')


def rows(path):
    data,partial=core.complete_rows(path)
    if partial or not Path(path).exists():raise ValueError('Expected complete original JSONL records')
    return data


def source_inputs():
    if sha(BUNDLE.read_bytes())!=BUNDLE_SHA:raise ValueError('Paired provenance bundle changed')
    bundle=read(BUNDLE);idle();files={str(BUNDLE):BUNDLE_SHA,**bundle['pins']};samples=[];schedule=[];receipts={};snapshots=[];original=None;protocol=None
    for name,h in files.items():
        if sha(Path(name).read_bytes())!=h:raise ValueError('Original paired provenance changed: '+name)
    for source,(sample,total,questions,schema) in zip(bundle['sources'],CASES):
        campaign=Path(source['campaign']);p=read(campaign/'plan.json');phase=next(x for x in p['phases'] if x['id']==source['phase']);directory=Path(phase['run_dir'])
        if p['protocol']!=schema or not core.frozen_ok(p) or p['candidate_files']!=source['original_dist']['files']:raise ValueError('Original executed candidate or source schema changed')
        if original is None:original=p
        if any(p[k]!=original[k] for k in ['namespace','eval_code_root','eval_commit','spec','env_file']) or p['eval_commit']!=tail.EVAL_COMMIT:raise ValueError('Original evaluator identity differs')
        data=read(phase['dataset']);manifest=read(directory/'manifest.json');config=read(campaign/'service.json')
        expected={'status':'finished','run_id':phase['run_id'],'memory_namespace':p['namespace'],'eval_commit':tail.EVAL_COMMIT,'samples':[sample],'planned_questions':questions,'dataset_sha256':sha(Path(phase['dataset']).read_bytes()),'mode':'proxy','answer_model':'glm-5.2','judge_model':'glm-5.2','judge_kind':'rubric','chunk_messages':20,'chunk_words':2000,'top_k':100,'add_transport_attempts':3,'answer_inference':{'stream':True,'max_completion_tokens':1800,'attempts':3},'service_config_sha256':sha(json.dumps(config,sort_keys=True,separators=(',',':')).encode()),'service_configuration':config}
        if len(data)!=1 or data[0]['sample_id']!=sample or any(manifest.get(k)!=v for k,v in expected.items()):raise ValueError('Completed sample manifest or configuration differs')
        shared={k:manifest[k] for k in ['answer_prompt_sha256','rubric_prompt_sha256','contract_sha256','split_sha256','conversion','answer_base','judge_base']}
        if protocol is not None and shared!=protocol:raise ValueError('Answer/Judge protocol differs between origins')
        protocol=shared
        own=tail.request_schedule(Path(p['eval_code_root']),Path(phase['dataset']),p['namespace']);latest,errors=core.terminal_ingest(rows(directory/'ingest.jsonl'))
        expected_ids={r['request_id'] for r in own};http=[r for r in rows(campaign/'http-receipts.jsonl') if r['request_id'] in expected_ids]
        if errors or len(own)!=total or set(latest)!=expected_ids or any(r['status']!='ok' for r in latest.values()) or Counter(r['request_id'] for r in http)!=Counter(expected_ids):raise ValueError('Complete successful original HTTP history is required')
        hashes={r['request_id']:r['hash'] for r in own}
        if any(r['status']!=200 or r['hash']!=hashes[r['request_id']] or not r['matches_schedule'] or (r['classification']=='prefix' and not r['matches_snapshot']) for r in http):raise ValueError('Original successful HTTP receipt differs')
        database=Path(p['data_dir'])/sha((p['namespace']+':memops:'+sample).encode())/'memory.sqlite'
        if str(database)!=source['source_database'] or sha(database.read_bytes())!=source['source_database_sha256']:raise ValueError('Completed source database changed')
        receipts.update(core.prefix_receipts(database,own,p['namespace'],sample,total));samples+=data;schedule+=own
        snapshots.append({'sample_id':sample,'revision':total,'source':str(database),'source_sha256':sha(database.read_bytes()),'logical_state':fixed.logical_state(database)})
        if {q['qid']:sha(q['question'].encode()) for q in data[0]['questions']}!={q['qid']:q['query_sha256'] for q in source['queries']}:raise ValueError('Original queries changed')
        for f in [campaign/'plan.json',campaign/'runtime.json',campaign/'service.json',campaign/'http-receipts.jsonl',directory/'manifest.json',directory/'ingest.jsonl',Path(phase['dataset'])]:files[str(f)]=sha(f.read_bytes())
        files.update(p['origin_files']);files.update(p['dependencies']);files.update(p['pinned_files'])
    partition(samples,schedule,receipts)
    return original,samples,schedule,receipts,snapshots,files


def candidate():
    manifest=CANDIDATE/'final-manifest.json';data=read(manifest);dist=CANDIDATE/'candidate-dist';actual=fixed.all_hashes(dist)
    if sha(manifest.read_bytes())!=MANIFEST_SHA or data['commit']!=COMMIT or actual!=data['dist'] or len(actual)!=144:raise ValueError('Expected the exact reviewed 144-file focus candidate')
    return dist,manifest,actual


def phase_definition(campaign):
    run_id=campaign.name+'-candidate-memops'
    return {'id':'completed','samples':[c[0] for c in CASES],'dataset':str(campaign/'samples.json'),'dataset_sha256':sha((campaign/'samples.json').read_bytes()),'planned_questions':16,'run_id':run_id,'run_dir':str(ROOT/'eval/artifacts'/run_id),'timeout_seconds':3600}


def prepare(args):
    campaign=ROOT/'artifacts'/tail.name(args.campaign)
    if campaign.exists():raise FileExistsError('Choose a new campaign; no output reuse')
    origin,samples,schedule,receipts,snapshots,files=source_inputs();dist,manifest,hashes=candidate();adapted=adapt_core(BASE_CORE.read_bytes())
    if args.port!=8125:raise ValueError('Use the reviewed independent port 8125')
    campaign.mkdir(mode=0o700);write(campaign/'samples.json',samples);write(campaign/'schedule.json',schedule);write(campaign/'receipts.private.json',receipts)
    phases=[phase_definition(campaign)];core.fresh(campaign,phases)
    for snapshot in snapshots:
        target=campaign/(snapshot['sample_id']+'.sqlite')
        with closing(sqlite3.connect(Path(snapshot['source']).as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(target)) as dst:src.backup(dst)
        if fixed.logical_state(target)!=snapshot['logical_state']:raise ValueError('Source changed during capture')
        snapshot.update(path=str(target),sha256=sha(target.read_bytes()))
    shutil.copytree(dist,campaign/'service-dist');shutil.copy2(manifest,campaign/'candidate-manifest.json')
    (campaign/'node_modules').symlink_to(Path(origin['eval_code_root']).parent/'service/node_modules',target_is_directory=True);write(campaign/'package.json',{'type':'module'})
    (campaign/'observe-search.mjs').write_text(core.qa.OBSERVER);(campaign/'service-launcher.mjs').write_text(core.launcher(campaign))
    config={**read(Path(origin['candidate_dist']).parent/'service.json'),**OVERRIDE};write(campaign/'focus-service-config.json',config)
    executor=campaign/'executor';probes=executor/'scripts/probes';probes.mkdir(parents=True)
    for f in [HERE,Path(core.qa.__file__),Path(fixed.__file__),Path(tail.__file__)]:shutil.copy2(f,probes/f.name)
    (probes/BASE_CORE.name).write_text(adapted);shutil.copy2(Path(sys.modules['experiment_runtime'].__file__),executor/'scripts/experiment_runtime.py');write(executor/'workspace-root.json',{'root':str(ROOT)})
    dependencies={str(f):sha(f.read_bytes()) for f in executor.rglob('*') if f.is_file()}
    dependencies.update({str(campaign/f):sha((campaign/f).read_bytes()) for f in ['package.json','observe-search.mjs','service-launcher.mjs']})
    pinned={str(campaign/f):sha((campaign/f).read_bytes()) for f in ['samples.json','schedule.json','receipts.private.json','candidate-manifest.json','focus-service-config.json',*[Path(s['path']).name for s in snapshots]]}
    plan={'protocol':'completed-query-focus-qa-v1','campaign':campaign.name,'created_at':core.now(),'scope':SCOPE,'entry_point':str(probes/HERE.name),'namespace':origin['namespace'],'eval_code_root':origin['eval_code_root'],'eval_commit':tail.EVAL_COMMIT,'spec':origin['spec'],'env_file':origin['env_file'],'candidate_commit':COMMIT,'candidate_manifest_sha256':MANIFEST_SHA,'candidate_dist':str(campaign/'service-dist'),'candidate_files':hashes,'origin_service_config':str(campaign/'focus-service-config.json'),'config_override':OVERRIDE,'port':args.port,'data_dir':str(campaign/'data'),'phases':phases,'planned_questions':16,'prefix_receipts':152,'total_adds':152,'new_tail_adds':0,'snapshots':snapshots,'origin_files':files,'dependencies':dependencies,'pinned_files':pinned,'core_source_sha256':CORE_SHA,'adapted_core_sha256':sha(adapted.encode())}
    write(campaign/'plan.json',plan);result=validate(campaign);write(campaign/'validation.json',result);return result


def validate(campaign,allow_owned_launch=False):
    plan=read(campaign/'plan.json');origin,samples,schedule,receipts,snapshots,files=source_inputs();_,_,hashes=candidate()
    expected={'protocol':'completed-query-focus-qa-v1','campaign':campaign.name,'entry_point':str(campaign/'executor/scripts/probes'/HERE.name),'namespace':origin['namespace'],'eval_code_root':origin['eval_code_root'],'eval_commit':tail.EVAL_COMMIT,'spec':origin['spec'],'env_file':origin['env_file'],'candidate_commit':COMMIT,'candidate_manifest_sha256':MANIFEST_SHA,'candidate_dist':str(campaign/'service-dist'),'candidate_files':hashes,'origin_service_config':str(campaign/'focus-service-config.json'),'config_override':OVERRIDE,'port':8125,'data_dir':str(campaign/'data'),'phases':[phase_definition(campaign)],'planned_questions':16,'prefix_receipts':152,'total_adds':152,'new_tail_adds':0,'origin_files':files,'core_source_sha256':CORE_SHA,'adapted_core_sha256':sha(adapt_core(BASE_CORE.read_bytes()).encode())}
    if any(plan.get(k)!=v for k,v in expected.items()) or not core.frozen_ok(plan):raise ValueError('Frozen plan identity, counts or artifacts changed')
    executor=campaign/'executor';actual=[HERE,CORE,Path(core.qa.__file__),Path(fixed.__file__),Path(tail.__file__),Path(sys.modules['experiment_runtime'].__file__)]
    required=[executor/'scripts/probes'/f.name for f in actual[:-1]]+[executor/'scripts/experiment_runtime.py',executor/'workspace-root.json']
    if any(str(f) not in plan['dependencies'] for f in required) or (FROZEN and actual!=required[:-1]):raise ValueError('Actual executor must use every frozen helper')
    if sha((executor/'scripts/probes'/BASE_CORE.name).read_bytes())!=plan['adapted_core_sha256']:raise ValueError('Reviewed core adaptations changed')
    if read(plan['origin_service_config'])!={**read(Path(origin['candidate_dist']).parent/'service.json'),**OVERRIDE}:raise ValueError('Only the exact enabled focus override is allowed')
    if read(campaign/'samples.json')!=samples or read(campaign/'schedule.json')!=schedule or read(campaign/'receipts.private.json')!=receipts or len(plan['snapshots'])!=3:raise ValueError('Original data or full prefix changed')
    if tail.request_schedule(Path(plan['eval_code_root']),campaign/'samples.json',plan['namespace'])!=schedule:raise ValueError('Actual frozen evaluator schedule differs')
    for frozen,current in zip(plan['snapshots'],snapshots):
        if any(frozen.get(k)!=v for k,v in current.items()) or sha(Path(frozen['path']).read_bytes())!=frozen['sha256'] or fixed.logical_state(frozen['path'])!=current['logical_state']:raise ValueError('Source or captured database changed')
        core.prefix_receipts(frozen['path'],schedule,plan['namespace'],current['sample_id'],current['revision'])
    core.fresh(campaign,plan['phases'],allow_owned_launch)
    return {'validation':'passed','model_calls_made':0,'compiled_files':144,'planned_questions':16,'prefix_receipts':152,'new_tail_adds':0,'origin_runtime_statuses':idle(),'entry_point':plan['entry_point']}


def search_generation(calls,traces,samples,namespace):
    queries={'search:'+sha(q['question'].encode()):{'user_id':namespace+':memops:'+s['sample_id'],'query':q['question']} for s in samples for q in s['questions']};by_trace={r.get('trace_id'):r for r in traces};reasons=[];focus=[]
    for call in calls:
        if call.get('kind')=='embedding' and call.get('action')=='add':reasons.append('unexpected_add_embedding')
        if call.get('kind')!='generation':continue
        if call.get('purpose') not in ('query_focus','rerank'):reasons.append('unexpected_write_generation');continue
        trace=by_trace.get(call.get('trace_id'),{});identity=trace.get('identity',{});expected=queries.get(identity.get('request_id'))
        if expected is None or identity.get('user_id')!=expected['user_id'] or trace.get('purpose')!=call['purpose']:reasons.append('unknown_search_generation')
        if call['purpose']=='query_focus':
            focus.append(call)
            try:valid=expected is not None and json.loads(trace['input'])=={'query':expected['query']} and call.get('attempt')==0 and call.get('transport_attempt_limit')==1
            except (KeyError,ValueError):valid=False
            if not valid:reasons.append('query_focus_input_or_attempt_changed')
    return {'search_query_focus_generation_calls':len(focus),'search_query_focus_outcomes':dict(Counter(r.get('outcome') for r in focus)),'write_generation_calls':sum(r.get('kind')=='generation' and r.get('purpose') not in ('query_focus','rerank') for r in calls),'add_embedding_calls':sum(r.get('kind')=='embedding' and r.get('action')=='add' for r in calls)},reasons


def phase_result(campaign,plan,phase):
    result=ORIGINAL_PHASE_RESULT(campaign,plan,phase)
    calls,partial_calls=core.complete_rows(campaign/'model-calls.jsonl');traces,partial_traces=core.complete_rows(campaign/'model-trace.jsonl')
    counts,reasons=search_generation(calls,traces,read(phase['dataset']),plan['namespace']);result.update(counts)
    if partial_calls or partial_traces:reasons.append('incomplete_model_audit')
    if reasons:result['integrity']='fail';result['reasons'].extend(reasons)
    return result


ORIGINAL_PHASE_RESULT=core.phase_result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--campaign',default=CAMPAIGN);parser.add_argument('--port',type=int,default=8125);parser.add_argument('--detach',action='store_true')
    actions=parser.add_mutually_exclusive_group(required=True)
    for action in ['preflight','plan','validate','run']:actions.add_argument('--'+action,action='store_true')
    args=parser.parse_args();os.umask(0o077)
    if args.detach and not args.run:parser.error('--detach requires --run')
    if args.preflight:
        _,samples,schedule,receipts,_,_=source_inputs();candidate();adapt_core(BASE_CORE.read_bytes());print(json.dumps({'validation':'passed','model_calls_made':0,'samples':len(samples),'planned_questions':16,'prefix_receipts':len(receipts),'total_adds':len(schedule),'new_tail_adds':0,'origin_runtime_statuses':idle()}));return
    if args.plan:print(json.dumps(prepare(args),indent=2));return
    campaign=ROOT/'artifacts'/tail.name(args.campaign);plan=read(campaign/'plan.json');entry=Path(plan['entry_point'])
    if HERE!=entry:os.execv(sys.executable,[sys.executable,str(entry),'--campaign',args.campaign,'--run' if args.run else '--validate',*(['--detach'] if args.detach else [])])
    core.validate=validate;core.idle=idle;core.COMMIT=COMMIT;core.SCOPE=SCOPE;core.phase_result=phase_result
    if args.validate:print(json.dumps(validate(campaign),indent=2))
    elif args.detach:validate(campaign);print(json.dumps(core.launch_detached([sys.executable,str(entry),'--campaign',args.campaign,'--run'],campaign/'runtime.json',cwd=ROOT),indent=2))
    else:core.execute(campaign)


if __name__=='__main__':
    try:main()
    except core.RunInterrupted as error:sys.exit(128+error.signum)
