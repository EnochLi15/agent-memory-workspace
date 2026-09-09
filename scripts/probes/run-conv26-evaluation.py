"""One original conv-26 LoCoMo phase using the existing priority campaign runner."""
import argparse, importlib.util, json, os, shutil, socket, sqlite3, subprocess, sys, time
from collections import Counter
from contextlib import closing
from pathlib import Path

HERE=Path(__file__).resolve();ROOT=HERE.parents[2];FROZEN=(ROOT/'workspace-root.json').exists()
if FROZEN:ROOT=Path(json.loads((ROOT/'workspace-root.json').read_text())['root'])
PROBES=HERE.parent if FROZEN else ROOT/'scripts/probes'
sys.path.insert(0,str(PROBES.parent))
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
core=module('conv26_priority_core',PROBES/'run-priority-campaign.py');core.ROOT=ROOT
HELPER=PROBES/'run-event-recovery-evaluation.py' if FROZEN else ROOT/'artifacts/priority-event-recovery-20260909-01/executor/scripts/probes/run-event-recovery-evaluation.py'
helper=module('conv26_frozen_helpers',HELPER);tail,fixed=helper.tail,helper.fixed
sha,read,write=core.sha,helper.read_json,core.write_json
CAMPAIGN='priority-conv26-20260909-01';COMMIT='292ea74328d1d76aadc23f0a758a5b633300f8ba'
CANDIDATE=ROOT/'artifacts/priority-transition-batching-20260909'
MANIFEST_SHA='aa2cce509f9ea5bc66d23a40a8e67935c622b90a8349c958197d203bcd809b94'
CORE_SHA='d89f06bcd6e2c55b06a53a71f41103c5461ebb46183f7f24e61c7120e37aa463'
OVERRIDE={'MEMORY_QUERY_FOCUS':'true','MEMORY_QUERY_FOCUS_TIMEOUT_MS':'8000','MEMORY_VERIFICATION_RESPONSE_FORMAT':'json_object'}
ORIGINAL=ROOT/'artifacts/priority-full-20260908-01/plan.json'
ORIGINAL_SHA='db5f4d0399034a4142f0bf3f39b9a695888702d20461f6ef19ae42540e6544be'
DATASET_SHA='23e67bdf5d22ad9c880fe9c4cf7f5a9fe1d27aefb4f5a4478c0d027a09ffccbc'
SAMPLE_SHA='fdc9b7f12ae9048e32c220fd729f49b9d8ee4332cf541ba91bf8f220046ec2af'
QIDS_SHA='074256c86e66feb7b7b3afcdd11fa6dded81adaa7f30e48114c32d59f68bd239'
CATEGORIES={'4':69,'2':36,'1':24}
SCOPE='Complete original conv-26: 19 sessions, 419 messages, 28 fresh adds and 129 fixed questions (69 single-hop, 36 temporal, 24 multi-hop). Original LoCoMo refined Python Judge with local qwen3:14b; GLM-5.2 Answer.'


def adapted_core(raw):
    if sha(raw)!=CORE_SHA:raise ValueError('Priority core changed from reviewed source')
    text=raw.decode()
    old="""safe = json.loads(subprocess.check_output(['node', '--input-type=module', '-e',
                "import {configFromEnv} from './dist/config.js';const {llmKey,...safe}=configFromEnv();console.log(JSON.stringify(safe));"],
                cwd=code / 'service', env=env, text=True))"""
    changes=[(old,"safe = json.loads(Path(plan['service_config_base']).read_text())"),
             ("['node', str(code / 'service/dist/server.js')]","['node', str(campaign / 'service-launcher.mjs')]"),
             ('High-risk ingestion gate failed; remaining 935 questions were not launched','LoCoMo ingestion failed; all 129 planned questions retain their original denominator')]
    for before,after in changes:
        if text.count(before)!=1:raise ValueError('Expected one precise priority core adaptation')
        text=text.replace(before,after)
    return text


def source():
    origin=read(ORIGINAL)
    if sha(ORIGINAL.read_bytes())!=ORIGINAL_SHA:raise ValueError('Original campaign plan changed')
    locomo=next(p for p in origin['phases'] if p['id']=='locomo');raw=Path(locomo['data_file']).read_bytes()
    if sha(raw)!=locomo['dataset_sha256'] or sha(raw)!=DATASET_SHA:raise ValueError('Original LoCoMo dataset changed')
    samples=[s for s in json.loads(raw) if s['sample_id']=='conv-26'];validate_sample(samples)
    manifest=CANDIDATE/'final-manifest.json';data=read(manifest);dist=CANDIDATE/'service/dist'
    if sha(manifest.read_bytes())!=MANIFEST_SHA or data['commit']!=COMMIT or fixed.all_hashes(dist)!=data['dist'] or len(data['dist'])!=144:raise ValueError('Frozen integrated candidate changed')
    code=Path(origin['code_root']);pins={str(ORIGINAL):sha(ORIGINAL.read_bytes()),str(Path(locomo['data_file'])):sha(raw),str(manifest):MANIFEST_SHA}
    for relative,h in origin['frozen_code_hashes'].items():
        path=code/relative
        if sha(path.read_bytes())!=h:raise ValueError('Original frozen input changed')
        if relative.startswith(('eval/','configs/')):pins[str(path)]=h
    for path in [Path(origin['spec']),Path(origin['selection_manifest']),Path(origin['env_file'])]:
        pins[str(path)]=sha(path.read_bytes())
    return origin,samples,manifest,dist,data['dist'],pins


def validate_sample(samples):
    if len(samples)!=1:raise ValueError('Exactly one original conv-26 sample is required')
    s=samples[0]
    if s['sample_id']!='conv-26' or s['benchmark']!='locomo' or len(s['sessions'])!=19 or sum(len(x['messages']) for x in s['sessions'])!=419 or len(s['questions'])!=129 or len({q['qid'] for q in s['questions']})!=129:raise ValueError('Complete 19-session/419-message/129-question conv-26 required')
    if Counter(str(q['category']) for q in s['questions'])!=CATEGORIES:raise ValueError('Original conv-26 category counts changed')
    if sha(json.dumps([q['qid'] for q in s['questions']],ensure_ascii=False,separators=(',',':')).encode())!=QIDS_SHA:raise ValueError('Original conv-26 question order changed')
    if sha(json.dumps(samples,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode())!=SAMPLE_SHA:raise ValueError('Original complete conv-26 subset changed')


def effective_spec(origin):
    spec=read(origin['spec']);spec['profiles']['candidate']['environment'].update(OVERRIDE);return spec


def config_from_spec(spec,origin,campaign,port,dist):
    env=os.environ.copy()
    for line in Path(origin['env_file']).read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key,value=line.split('=',1);env[key.strip()]=value.strip().strip('"').strip("'")
    env.update(spec['defaults']);env.update(spec['profiles']['candidate']['environment']);env.update(PORT=str(port),HOST='127.0.0.1',MEMORY_DATA_DIR=str(campaign/'data'))
    program="const {configFromEnv}=await import(process.argv[1]);const {llmKey,...safe}=configFromEnv();console.log(JSON.stringify(safe));"
    return json.loads(subprocess.check_output(['node','--input-type=module','-e',program,(dist/'config.js').as_uri()],env=env,text=True))


def phase(campaign):
    run_id=campaign.name+'-candidate-locomo';data=campaign/'conv26.json'
    return {'id':'locomo','benchmark':'locomo','data_file':str(data),'dataset_sha256':sha(data.read_bytes()),'questions':129,'samples':1,'run_id':run_id,'run_dir':str(ROOT/'eval/artifacts'/run_id)}


def prepare(args):
    campaign=ROOT/'artifacts'/tail.name(args.campaign)
    if campaign.exists():raise FileExistsError('Preserve the existing campaign')
    origin,samples,manifest,dist,hashes,pins=source();raw_core=(ROOT/'scripts/probes/run-priority-campaign.py').read_bytes();patched=adapted_core(raw_core)
    if args.port!=8125:raise ValueError('Expected reviewed port 8125')
    with socket.socket() as probe:probe.bind(('127.0.0.1',args.port))
    campaign.mkdir(mode=0o700);write(campaign/'conv26.json',samples);namespace=campaign.name+'-candidate';code=campaign/'code';(code/'service').mkdir(parents=True);(code/'configs').mkdir()
    shutil.copytree(dist,code/'service/dist');shutil.copy2(CANDIDATE/'service/package.json',code/'service/package.json');shutil.copy2(CANDIDATE/'service/package-lock.json',code/'service/package-lock.json')
    (code/'service/node_modules').symlink_to((CANDIDATE/'service/node_modules').resolve(),target_is_directory=True);(code/'eval').symlink_to(Path(origin['code_root'])/'eval',target_is_directory=True)
    (campaign/'service-dist').symlink_to(code/'service/dist',target_is_directory=True)
    derived=effective_spec(origin);write(code/'configs/spec.json',derived);write(campaign/'service-base.json',config_from_spec(derived,origin,campaign,args.port,dist))
    schedule=tail.request_schedule(code/'eval',campaign/'conv26.json',namespace)
    if len(schedule)!=28 or len({r['request_id'] for r in schedule})!=28:raise ValueError('Frozen evaluator must generate exactly 28 requests')
    write(campaign/'schedule.json',schedule);write(campaign/'receipts.private.json',{})
    (campaign/'observe-search.mjs').write_text(helper.qa.OBSERVER);(campaign/'service-launcher.mjs').write_text(helper.launcher(campaign));shutil.copy2(manifest,campaign/'candidate-manifest.json')
    executor=campaign/'executor';probes=executor/'scripts/probes';probes.mkdir(parents=True)
    for file in [HERE,HELPER,Path(helper.qa.__file__),Path(fixed.__file__),Path(tail.__file__)]:shutil.copy2(file,probes/file.name)
    (probes/'run-priority-campaign.py').write_text(patched)
    for name in ['experiment_runtime','judge_runtime']:shutil.copy2(Path(sys.modules[name].__file__),executor/'scripts'/(name+'.py'))
    write(executor/'workspace-root.json',{'root':str(ROOT)})
    frozen={str(f.relative_to(code)):sha(f.read_bytes()) for f in (code/'service').rglob('*') if f.is_file() and 'node_modules' not in f.parts}
    for relative,h in origin['frozen_code_hashes'].items():
        if relative.startswith('eval/'):frozen[relative]=h
    frozen['configs/spec.json']=sha((code/'configs/spec.json').read_bytes())
    dependencies={str(f):sha(f.read_bytes()) for f in executor.rglob('*') if f.is_file()}
    dependencies.update({str(campaign/name):sha((campaign/name).read_bytes()) for name in ['conv26.json','schedule.json','receipts.private.json','service-base.json','observe-search.mjs','service-launcher.mjs','candidate-manifest.json']})
    plan={'protocol':'single-conv26-locomo-v1','campaign':campaign.name,'created_at':core.now(),'scope':SCOPE,'entry_point':str(probes/HERE.name),'code_root':str(code),'candidate_commit':COMMIT,'candidate_manifest_sha256':MANIFEST_SHA,'candidate_files':hashes,'namespace':namespace,'spec':str(code/'configs/spec.json'),'spec_sha256':frozen['configs/spec.json'],'env_file':origin['env_file'],'service_config_base':str(campaign/'service-base.json'),'configuration_override':OVERRIDE,'selection_manifest':origin['selection_manifest'],'selection_manifest_sha256':origin['selection_manifest_sha256'],'source_checkpoints':{'workspace':origin['source_checkpoints']['workspace'],'eval':origin['source_checkpoints']['eval'],'service':{'commit':COMMIT}},'frozen_code_hashes':frozen,'source_files':pins,'dependencies':dependencies,'port':args.port,'data_dir':str(campaign/'data'),'phases':[phase(campaign)],'planned_questions':129,'planned_adds':28,'timeout_seconds':10800,'core_source_sha256':CORE_SHA,'adapted_core_sha256':sha(patched.encode()),'judge_protocol':{'mode':'upstream-reproduction','judge_kind':'refined-python','judge_model':'qwen3:14b','answer_model':'glm-5.2'}}
    write(campaign/'plan.json',plan);spec,datasets=validate(campaign);result={'validation':'passed','model_calls_made':0,'planned_questions':129,'fresh_adds':28,'candidate_commit':COMMIT,'entry_point':plan['entry_point'],'judge_protocol':plan['judge_protocol']};write(campaign/'validation.json',result);return result


def validate(campaign,fresh=True):
    plan=read(campaign/'plan.json');origin,samples,_,_,hashes,pins=source();code=Path(plan['code_root'])
    expected={'protocol':'single-conv26-locomo-v1','campaign':campaign.name,'entry_point':str(campaign/'executor/scripts/probes'/HERE.name),'code_root':str(campaign/'code'),'candidate_commit':COMMIT,'candidate_manifest_sha256':MANIFEST_SHA,'candidate_files':hashes,'namespace':campaign.name+'-candidate','spec':str(campaign/'code/configs/spec.json'),'service_config_base':str(campaign/'service-base.json'),'configuration_override':OVERRIDE,'env_file':origin['env_file'],'port':8125,'data_dir':str(campaign/'data'),'phases':[phase(campaign)],'planned_questions':129,'planned_adds':28,'timeout_seconds':10800,'source_files':pins,'judge_protocol':{'mode':'upstream-reproduction','judge_kind':'refined-python','judge_model':'qwen3:14b','answer_model':'glm-5.2'}}
    if any(plan.get(k)!=v for k,v in expected.items()):raise ValueError('Frozen conv-26 plan differs')
    for file,h in {**plan['source_files'],**plan['dependencies'],**{str(code/k):v for k,v in plan['frozen_code_hashes'].items()}}.items():
        if sha(Path(file).read_bytes())!=h:raise ValueError('Frozen file changed: '+file)
    if fixed.all_hashes(code/'service/dist')!=hashes or read(plan['spec'])!=effective_spec(origin) or read(campaign/'conv26.json')!=samples:raise ValueError('Candidate, three configuration overrides or original sample changed')
    if (code/'eval').resolve()!=Path(origin['code_root'])/'eval' or (code/'service/node_modules').resolve()!=(CANDIDATE/'service/node_modules').resolve():raise ValueError('Shared runtime dependency target changed')
    schedule=tail.request_schedule(code/'eval',campaign/'conv26.json',plan['namespace'])
    if len(schedule)!=28 or schedule!=read(campaign/'schedule.json') or core.expected_requests(samples,plan['namespace'])!={r['request_id'] for r in schedule}:raise ValueError('Exact original schedule changed')
    cfg=read(plan['service_config_base']);expected_cfg=config_from_spec(effective_spec(origin),origin,campaign,plan['port'],CANDIDATE/'service/dist')
    if cfg!=expected_cfg or any(cfg[k]!=v for k,v in {'queryFocus':True,'queryFocusTimeout':8000,'verificationResponseFormat':'json_object','maxEvidence':32,'tokenBudget':6000,'candidateLimit':200,'rerank':False}.items()):raise ValueError('Effective service configuration drifted')
    paths=[HERE,Path(core.__file__),HELPER,Path(helper.qa.__file__),Path(fixed.__file__),Path(tail.__file__),Path(sys.modules['experiment_runtime'].__file__),Path(sys.modules['judge_runtime'].__file__)]
    needed=[campaign/'executor/scripts/probes'/p.name for p in paths[:6]]+[campaign/'executor/scripts'/p.name for p in paths[6:]]
    if any(str(p) not in plan['dependencies'] for p in needed) or (FROZEN and paths!=needed):raise ValueError('Actual imports must all use frozen executor files')
    if fresh:
        helper.fresh(campaign,[{'run_dir':plan['phases'][0]['run_dir']}],allow_owned_launch=True)
        if Path(plan['data_dir']).exists():raise FileExistsError('Fresh conv-26 cannot reuse storage')
    return read(plan['spec']),[samples]


BASE_SUMMARIZE=core.summarize
def summarize(plan,campaign,statuses):
    result=BASE_SUMMARIZE(plan,campaign,statuses);result.update(planned_questions=129,accuracy_over_planned=result['correct']/129,scope=SCOPE)
    judged={r['qid']:r for r in core.read_rows(Path(plan['phases'][0]['run_dir'])/'judgments.jsonl',live=True)}
    result['question_statuses']=[{'qid':q['qid'],'status':judged.get(q['qid'],{}).get('status','not_completed'),'correct':judged.get(q['qid'],{}).get('correct')} for s in read(campaign/'conv26.json') for q in s['questions']]
    return result


def storage_check(plan,campaign):
    schedule=read(campaign/'schedule.json');expected={r['request_id']:r['hash'] for r in schedule};directory=Path(plan['phases'][0]['run_dir'])
    latest,errors=helper.terminal_ingest(core.read_rows(directory/'ingest.jsonl'));ok={rid for rid,row in latest.items() if row.get('status')=='ok'}
    http=core.read_rows(campaign/'http-receipts.jsonl')
    if any(row.get('hash')!=expected.get(row.get('request_id')) or not row.get('matches_schedule') for row in http):errors.append('HTTP_request_hash_mismatch')
    if ok!={row['request_id'] for row in http if row['status']==200}:errors.append('HTTP_success_set_differs')
    user=plan['namespace']+':locomo:conv-26';db=Path(plan['data_dir'])/sha(user.encode())/'memory.sqlite';saved={};revision=0
    if db.exists():
        with closing(sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True)) as connection:
            if connection.execute('PRAGMA quick_check').fetchone()[0]!='ok':errors.append('sqlite_integrity_failed')
            meta=dict(connection.execute('SELECT key,value FROM meta'));revision=int(meta.get('revision',-1))
            if meta.get('user_id')!=user:errors.append('unexpected_database_tenant')
            saved={rid:(h,json.loads(receipt)) for rid,h,receipt in connection.execute('SELECT id,hash,receipt FROM requests')}
    if set(saved)!=ok or revision!=len(ok):errors.append('revision_or_receipt_set_mismatch')
    for rid,(h,receipt) in saved.items():
        if h!=expected.get(rid) or receipt!={'success':True,'request_id':rid,'user_id':user,'session_id':rid[len(user)+1:].rsplit(':',1)[0]}:errors.append('stored_receipt_hash_or_body_mismatch')
    queries={'search:'+sha(q['question'].encode()):q['question'] for s in read(campaign/'conv26.json') for q in s['questions']}
    traces={r.get('trace_id'):r for r in core.read_rows(campaign/'model-trace.jsonl')};calls=core.read_rows(campaign/'model-calls.jsonl');writes=focus=0
    for call in calls:
        if call.get('kind')!='generation':continue
        trace=traces.get(call.get('trace_id'),{});identity=trace.get('identity',{});rid=identity.get('request_id');purpose=call.get('purpose')
        if identity.get('user_id')!=user or trace.get('purpose')!=purpose:errors.append('model_trace_identity_mismatch')
        if purpose=='query_focus':
            focus+=1
            try:valid=rid in queries and json.loads(trace['input'])=={'query':queries[rid]} and call.get('attempt')==0 and call.get('transport_attempt_limit')==1
            except (ValueError,KeyError):valid=False
            if not valid:errors.append('focus_not_bound_to_original_search')
        else:
            writes+=1
            if rid not in expected:errors.append('write_model_outside_original_add_schedule')
    return {'integrity':'fail' if errors else 'pass','reasons':errors,'planned_adds':28,'successful_adds':len(ok),'revision':revision,'exact_receipts':len(saved),'physical_HTTP_responses':len(http),'write_generation_calls':writes,'search_query_focus_generation_calls':focus,'add_embedding_calls':sum(r.get('kind')=='embedding' and r.get('action')=='add' for r in calls),'scope':'Only actual successful original HTTP adds may have committed receipts; failed and unattempted requests remain outside the receipt set.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--campaign',default=CAMPAIGN);parser.add_argument('--port',type=int,default=8125);parser.add_argument('--detach',action='store_true');actions=parser.add_mutually_exclusive_group(required=True)
    for name in ['plan','validate','run','status']:actions.add_argument('--'+name,action='store_true')
    args=parser.parse_args();os.umask(0o077);campaign=ROOT/'artifacts'/tail.name(args.campaign)
    if args.detach and not args.run:parser.error('--detach requires --run')
    if args.plan:print(json.dumps(prepare(args),indent=2));return
    plan=read(campaign/'plan.json');entry=Path(plan['entry_point'])
    if HERE!=entry:os.execv(sys.executable,[sys.executable,str(entry),'--campaign',args.campaign,'--status' if args.status else '--validate' if args.validate else '--run',*(['--detach'] if args.detach else [])])
    core.summarize=summarize
    if args.status:
        saved=read(campaign/'phase-status.json') if (campaign/'phase-status.json').exists() else {'phases':[]};print(json.dumps(summarize(plan,campaign,{p['id']:p['status'] for p in saved['phases']}),indent=2));return
    spec,datasets=validate(campaign)
    if args.validate:print(json.dumps({'validation':'passed','model_calls_made':0,'planned_questions':129,'fresh_adds':28}));return
    if args.detach:print(json.dumps(core.launch_detached([sys.executable,str(entry),'--campaign',args.campaign,'--run'],campaign/'runtime.json',cwd=ROOT)));return
    class TimedRun(core.ManagedRun):
        def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self.deadline=time.monotonic()+plan['timeout_seconds']
        def poll(self,check_required=True):
            if check_required and time.monotonic()>self.deadline:raise TimeoutError('Complete conv-26 run exceeded its pinned deadline')
            return super().poll(check_required)
    core.ManagedRun=TimedRun
    try:core.run_campaign(plan,campaign,spec,datasets)
    finally:
        saved=read(campaign/'phase-status.json') if (campaign/'phase-status.json').exists() else {'phases':[]}
        result=summarize(plan,campaign,{p['id']:p['status'] for p in saved['phases']});result['runtime']=core.read_status(campaign/'runtime.json') if (campaign/'runtime.json').exists() else {'status':'not_started','alive':False};result['storage']=storage_check(plan,campaign);write(campaign/'final-status.json',result)
        validate(campaign,False)
        if result['storage']['integrity']!='pass':raise ValueError('Final committed receipt integrity failed')


if __name__=='__main__':
    try:main()
    except core.RunInterrupted as error:sys.exit(128+error.signum)
