"""Single B03 tail adapter over the frozen recovery executor. Planning is zero-cloud."""
import argparse, importlib.util, json, os, shutil, sqlite3, subprocess, sys
from contextlib import closing
from pathlib import Path
HERE=Path(__file__).resolve();ROOT=HERE.parents[2]
if (ROOT/'workspace-root.json').exists():ROOT=Path(json.loads((ROOT/'workspace-root.json').read_text())['root'])
CORE=HERE.with_name('run-event-recovery-evaluation.py') if (HERE.parents[2]/'workspace-root.json').exists() else ROOT/'artifacts/priority-event-recovery-20260909-01/executor/scripts/probes/run-event-recovery-evaluation.py'
sys.path.insert(0,str(CORE.parents[1]))
spec=importlib.util.spec_from_file_location('b03_frozen_core',CORE);core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)
fixed,tail=core.fixed,core.tail
sha,read,write=core.sha,core.read_json,core.write_json
SAMPLE='B03_remember';CAMPAIGN='priority-B03-tail-qa-20260909-01';PREVIOUS=ROOT/'artifacts/priority-B03-segment34-add-20260909-01'
SCOPE='B03 original complete 50-add history and six fixed questions; resume from the exact committed prefix after a real successful single add. No rewritten questions or previous-score changes.'


def previous_path(value):
    base=(ROOT/'artifacts').resolve();path=Path(value)
    if not path.is_absolute():path=base/tail.name(str(path))
    if path.parent!=base or path.is_symlink() or path.resolve().parent!=base:raise ValueError('Previous must be a direct workspace artifacts child')
    return path.resolve()


def idle(previous):
    state=core.read_status(previous/'runtime.json')
    if state['status'] not in ('finished','failed','interrupted') or state.get('alive') or any(c.get('alive') for c in state.get('children',{}).values()):raise ValueError('Single-add controller and children must be terminal')
    return state['status']


def partition(samples,schedule):
    selected=[s for s in samples if s['sample_id']==SAMPLE]
    if len(selected)!=1 or selected[0]['benchmark']!='memops' or len(selected[0]['sessions'])!=50 or len(selected[0]['questions'])!=6 or len({q['qid'] for q in selected[0]['questions']})!=6:raise ValueError('Exactly the original B03 50 sessions and six unique questions are required')
    own=[r for r in schedule if r['sample_id']==SAMPLE]
    if len(own)!=50 or len({r['request_id'] for r in own})!=50 or not own[33]['request_id'].endswith(':segment-34:0') or not own[34]['request_id'].endswith(':segment-35:0'):raise ValueError('Expected 34 prefix receipts and 16 original tail adds')
    return selected,own


def successful_prefix(database,case,prefix,result,schedule,namespace):
    if result.get('http_status')!=200 or result.get('http_attempts')!=1:raise ValueError('Actual single-add HTTP 200 is required before planning')
    revision=case.get('revision');own=[r for r in schedule if r['sample_id']==SAMPLE]
    if type(revision) is not int or not 0<=revision<len(own) or case['sample_id']!=SAMPLE or (case['request_id'],case['request_sha256'])!=(own[revision]['request_id'],own[revision]['hash']):raise ValueError('Single-add case must match its exact original schedule position')
    check=fixed.database_result(database,case,prefix,result)
    if check['integrity']!='pass' or check['revision_after']!=revision+1:raise ValueError(f'Successful add must have its exact receipt and revision {revision+1}')
    return core.prefix_receipts(database,schedule,namespace,SAMPLE,revision+1)


def source(previous):
    previous=previous_path(previous);idle(previous);p=read(previous/'plan.json')
    if p['protocol']!='fixed-failed-adds-http-v1' or p['planned_adds']!=1:raise ValueError('Expected the single B03 failed-add probe')
    data,origin,_,prefix=fixed.validate_inputs(p['inputs'],p['inputs_sha256']);case=data['cases'][0]
    if len(data['cases'])!=1 or case['sample_id']!=SAMPLE:raise ValueError('Single-add source identity changed')
    results=read(previous/'results.private.json')
    if len(results)!=1 or read(previous/'http-results/0.private.json')!=results[0]:raise ValueError('Original raw successful HTTP result is required')
    phase=next(x for x in origin['phases'] if x['id']=='memops-risk');eval_root=Path(origin['code_root'])/'eval'
    samples,schedule=partition(read(phase['data_file']),tail.request_schedule(eval_root,Path(phase['data_file']),data['namespace']))
    database=Path(p['data_dir'])/sha(case['request']['user_id'].encode())/'memory.sqlite'
    receipts=successful_prefix(database,case,prefix,results[0],schedule,data['namespace'])
    dist,hashes,provenance=fixed.candidate(p['candidate_dist'],p['candidate_manifest'],p['candidate_provenance']['commit'])
    if hashes!=p['candidate_files']:raise ValueError('Executed successful-add candidate changed')
    paths=[previous/f for f in ['plan.json','results.private.json','http-results/0.private.json','service.json','runtime.json']]+[Path(p['inputs']),Path(p['candidate_manifest']),Path(origin['spec']),Path(phase['data_file'])]
    files={str(f):sha(f.read_bytes()) for f in paths};files.update(p['execution_files'])
    dependencies={str(Path(origin['code_root'])/f):h for f,h in origin['frozen_code_hashes'].items()}
    dependencies.update({str(f):sha(f.read_bytes()) for d in ['dist','python'] for f in (eval_root/d).rglob('*') if f.is_file() and '__pycache__' not in f.parts and f.suffix!='.pyc'})
    snapshot={'sample_id':SAMPLE,'revision':case['revision']+1,'source':str(database),'source_sha256':sha(database.read_bytes()),'logical_state':fixed.logical_state(database)}
    return p,origin,samples,schedule,receipts,snapshot,files,dependencies


def prepare(args):
    campaign=ROOT/'artifacts'/tail.name(args.campaign)
    if campaign.exists():raise FileExistsError('Choose a new campaign; no output reuse')
    source_dir=previous_path(args.previous);previous,origin,samples,schedule,receipts,snapshot,files,dependencies=source(source_dir)
    if not 1024<=args.port<=65535 or args.port in (8115,8116,8117,8118,8119,8120):raise ValueError('Use an independent port')
    campaign.mkdir(mode=0o700);write(campaign/'samples.json',samples);write(campaign/'schedule.json',schedule);write(campaign/'receipts.private.json',receipts)
    captured=campaign/f"B03-rev{snapshot['revision']}.sqlite"
    with closing(sqlite3.connect(Path(snapshot['source']).as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(captured)) as dst:src.backup(dst)
    if fixed.logical_state(captured)!=snapshot['logical_state']:raise ValueError('Source changed during capture')
    snapshot.update(path=str(captured),sha256=sha(captured.read_bytes()))
    shutil.copytree(previous['candidate_dist'],campaign/'service-dist');shutil.copy2(previous['candidate_manifest'],campaign/'candidate-manifest.json')
    (campaign/'node_modules').symlink_to(Path(origin['code_root'])/'service/node_modules',target_is_directory=True);write(campaign/'package.json',{'type':'module'})
    (campaign/'observe-search.mjs').write_text(core.qa.OBSERVER);(campaign/'service-launcher.mjs').write_text(core.launcher(campaign))
    executor=campaign/'executor';probes=executor/'scripts/probes';probes.mkdir(parents=True)
    for f in [HERE,CORE,Path(core.qa.__file__),Path(fixed.__file__),Path(tail.__file__)]:shutil.copy2(f,probes/f.name)
    copied=probes/CORE.name;text=copied.read_text();needle="'scope':SCOPE,'planned_questions':16,'correct'"
    if text.count(needle)!=1:raise ValueError('Frozen executor summary shape changed')
    copied.write_text(text.replace(needle,"'scope':SCOPE,'planned_questions':plan['planned_questions'],'correct'"))
    shutil.copy2(Path(sys.modules['experiment_runtime'].__file__),executor/'scripts/experiment_runtime.py');write(executor/'workspace-root.json',{'root':str(ROOT)})
    dependencies.update({str(f):sha(f.read_bytes()) for f in executor.rglob('*') if f.is_file()})
    dependencies.update({str(campaign/f):sha((campaign/f).read_bytes()) for f in ['package.json','observe-search.mjs','service-launcher.mjs']})
    run_id=campaign.name+'-candidate-memops';phase={'id':'b03-tail','samples':[SAMPLE],'dataset':str(campaign/'samples.json'),'dataset_sha256':sha((campaign/'samples.json').read_bytes()),'planned_questions':len(samples[0]['questions']),'run_id':run_id,'run_dir':str(ROOT/'eval/artifacts'/run_id),'timeout_seconds':10800}
    pinned={str(campaign/f):sha((campaign/f).read_bytes()) for f in ['samples.json','schedule.json','receipts.private.json','candidate-manifest.json',captured.name]}
    plan={'protocol':'single-B03-tail-qa-v1','campaign':campaign.name,'created_at':core.now(),'scope':SCOPE,'entry_point':str(probes/HERE.name),'previous':str(source_dir),'namespace':origin['namespace'],'eval_code_root':str(Path(origin['code_root'])/'eval'),'eval_commit':tail.EVAL_COMMIT,'spec':origin['spec'],'env_file':origin['env_file'],'candidate_commit':previous['candidate_provenance']['commit'],'candidate_dist':str(campaign/'service-dist'),'candidate_files':previous['candidate_files'],'origin_service_config':str(source_dir/'service.json'),'port':args.port,'data_dir':str(campaign/'data'),'phases':[phase],'planned_questions':len(samples[0]['questions']),'prefix_receipts':len(receipts),'total_adds':len(schedule),'new_tail_adds':len(schedule)-len(receipts),'snapshots':[snapshot],'origin_files':files,'dependencies':dependencies,'pinned_files':pinned}
    write(campaign/'plan.json',plan);result=validate(campaign);write(campaign/'validation.json',result);return result


def validate(campaign,allow_owned_launch=False):
    plan=read(campaign/'plan.json');source_dir=previous_path(plan['previous']);previous,origin,samples,schedule,receipts,snapshot,_,_=source(source_dir)
    if type(plan.get('port')) is not int or not 1024<=plan['port']<=65535 or plan['port'] in (8115,8116,8117,8118,8119,8120):raise ValueError('Independent port changed')
    if plan['protocol']!='single-B03-tail-qa-v1' or plan['campaign']!=campaign.name or (plan['planned_questions'],plan['prefix_receipts'],plan['total_adds'],plan['new_tail_adds'])!=(len(samples[0]['questions']),len(receipts),len(schedule),len(schedule)-len(receipts)):raise ValueError('Single-sample identity or denominator changed')
    phase={'id':'b03-tail','samples':[SAMPLE],'dataset':str(campaign/'samples.json'),'dataset_sha256':sha((campaign/'samples.json').read_bytes()),'planned_questions':len(samples[0]['questions']),'run_id':campaign.name+'-candidate-memops','run_dir':str(ROOT/'eval/artifacts'/(campaign.name+'-candidate-memops')),'timeout_seconds':10800}
    wanted={'entry_point':str(campaign/'executor/scripts/probes'/HERE.name),'previous':str(source_dir),'namespace':origin['namespace'],'eval_code_root':str(Path(origin['code_root'])/'eval'),'eval_commit':tail.EVAL_COMMIT,'spec':origin['spec'],'env_file':origin['env_file'],'origin_service_config':str(source_dir/'service.json'),'data_dir':str(campaign/'data'),'candidate_dist':str(campaign/'service-dist'),'candidate_commit':previous['candidate_provenance']['commit'],'candidate_files':previous['candidate_files'],'phases':[phase]}
    if any(plan.get(k)!=v for k,v in wanted.items()) or not core.frozen_ok(plan):raise ValueError('Pinned inputs, candidate or execution changed')
    core.fresh(campaign,plan['phases'],allow_owned_launch)
    if read(campaign/'samples.json')!=samples or read(campaign/'schedule.json')!=schedule or read(campaign/'receipts.private.json')!=receipts or len(plan['snapshots'])!=1:raise ValueError('Original data or prefix changed')
    frozen=plan['snapshots'][0]
    if any(frozen.get(k)!=v for k,v in snapshot.items()) or sha(Path(frozen['path']).read_bytes())!=frozen['sha256'] or fixed.logical_state(frozen['path'])!=snapshot['logical_state']:raise ValueError('Actual successful source or captured snapshot changed')
    if core.prefix_receipts(frozen['path'],schedule,origin['namespace'],SAMPLE,snapshot['revision'])!=receipts:raise ValueError('Captured prefix differs')
    return {'validation':'passed','model_calls_made':0,'candidate_commit':plan['candidate_commit'],'planned_questions':len(samples[0]['questions']),'prefix_receipts':len(receipts),'new_tail_adds':len(schedule)-len(receipts),'total_adds':len(schedule),'previous_status':idle(source_dir),'entry_point':plan['entry_point']}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--campaign',default=CAMPAIGN);parser.add_argument('--port',type=int,default=8125);parser.add_argument('--previous',default=str(PREVIOUS));parser.add_argument('--detach',action='store_true')
    actions=parser.add_mutually_exclusive_group(required=True)
    for action in ['plan','validate','run']:actions.add_argument('--'+action,action='store_true')
    args=parser.parse_args();os.umask(0o077);campaign=ROOT/'artifacts'/tail.name(args.campaign)
    if args.detach and not args.run:parser.error('--detach requires --run')
    if args.plan:print(json.dumps(prepare(args),indent=2));return
    plan=read(campaign/'plan.json');entry=Path(plan['entry_point'])
    if HERE!=entry:os.execv(sys.executable,[sys.executable,str(entry),'--campaign',args.campaign,'--run' if args.run else '--validate',*(['--detach'] if args.detach else [])])
    source_dir=previous_path(plan['previous']);core.validate=validate;core.idle=lambda:idle(source_dir);core.COMMIT=plan['candidate_commit'];core.SCOPE=SCOPE
    if args.validate:print(json.dumps(validate(campaign),indent=2))
    elif args.detach:validate(campaign);print(json.dumps(core.launch_detached([sys.executable,str(entry),'--campaign',args.campaign,'--run'],campaign/'runtime.json',cwd=ROOT),indent=2))
    else:core.execute(campaign)


if __name__=='__main__':
    try:main()
    except core.RunInterrupted as error:sys.exit(128+error.signum)
