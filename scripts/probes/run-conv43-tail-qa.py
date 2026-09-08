"""Resume the exact conv-43 revision-38 prefix with the frozen LoCoMo executor."""
import argparse, hashlib, json, os, shutil, sqlite3, sys, types
from contextlib import closing
from pathlib import Path
HERE=Path(__file__).resolve();ROOT=HERE.parents[2];FROZEN=(ROOT/'workspace-root.json').exists()
if FROZEN:ROOT=Path(json.loads((ROOT/'workspace-root.json').read_text())['root'])
PREVIOUS=ROOT/'artifacts/priority-conv43-20260909-01';CAMPAIGN='priority-conv43-tail-qa-20260909-01'
BASE=HERE.with_name('run-conv43-base.py') if FROZEN else PREVIOUS/'executor/scripts/probes/run-conv43-evaluation.py'
BASE_SHA='289358edcfa9150a07d8bd49c6ead42ce82611cef96a72e6ea295a88bac43881'
sha=lambda raw:hashlib.sha256(raw).hexdigest()

def adapted_base(raw):
    if sha(raw)!=BASE_SHA:raise ValueError('Reviewed frozen conv-43 entry changed')
    text=raw.decode()
    for before,after in [("namespace=campaign.name+'-candidate'","namespace=RESUME_NAMESPACE"),("'namespace':campaign.name+'-candidate'","'namespace':RESUME_NAMESPACE")]:
        if text.count(before)!=1:raise ValueError('Expected one exact namespace adaptation')
        text=text.replace(before,after)
    if text.count("'fresh_adds':43")!=2:raise ValueError('Expected two precise fresh-add counters')
    return text.replace("'fresh_adds':43", "'fresh_adds':5,'prefix_adds':38")

b=types.ModuleType('conv43_resume_base');b.__file__=str(BASE);exec(compile(adapted_base(BASE.read_bytes()),str(BASE),'exec'),b.__dict__)
read,write,fixed,tail=b.read,b.write,b.fixed,b.tail
BASE_VALIDATE,BASE_STORAGE=b.validate,b.storage_check
SCOPE='Original conv-43 38 committed prefix adds plus five new tail adds, then 47 fixed LoCoMo questions. Original namespace and complete history; no previous answers are rejudged.'

def receipt_state(path,schedule,namespace,revision):
    user=namespace+':locomo:conv-43';expected=schedule[:revision]
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('SQLite integrity failed')
        meta=dict(db.execute('SELECT key,value FROM meta'));saved={rid:{'hash':h,'receipt':json.loads(r)} for rid,h,r in db.execute('SELECT id,hash,receipt FROM requests')}
    if meta.get('user_id')!=user or meta.get('source_format')!='dual-source-v5-s1' or int(meta.get('revision',-1))!=revision or set(saved)!={r['request_id'] for r in expected}:raise ValueError('Exact committed revision and prefix required')
    for row in expected:
        rid=row['request_id'];receipt={'success':True,'request_id':rid,'user_id':user,'session_id':rid[len(user)+1:].rsplit(':',1)[0]}
        if saved[rid]!={'hash':row['hash'],'receipt':receipt}:raise ValueError('Original prefix hash or receipt changed')
    return saved


def source():
    p=read(PREVIOUS/'plan.json');state=b.core.read_status(PREVIOUS/'runtime.json')
    if state['status']!='failed' or state.get('alive') or any(c.get('alive') for c in state.get('children',{}).values()):raise ValueError('Original failed controller and all owned children must be terminal')
    if p['protocol']!='single-conv43-locomo-v1' or (p['planned_adds'],p['planned_questions'])!=(43,47):raise ValueError('Original conv-43 execution changed')
    pins={**p['source_files'],**p['dependencies'],**{str(Path(p['code_root'])/k):v for k,v in p['frozen_code_hashes'].items()}}
    if any(sha(Path(f).read_bytes())!=h for f,h in pins.items()):raise ValueError('Original pinned files changed')
    samples=read(PREVIOUS/'conv43.json');b.validate_sample(samples);schedule=tail.request_schedule(Path(p['code_root'])/'eval',PREVIOUS/'conv43.json',p['namespace'])
    if schedule!=read(PREVIOUS/'schedule.json') or len(schedule)!=43 or not schedule[38]['request_id'].endswith(':session-27:0'):raise ValueError('Exact original 43-request schedule required')
    run=Path(p['phases'][0]['run_dir']);manifest=read(run/'manifest.json');rows=b.core.read_rows(run/'ingest.jsonl');latest,errors=b.helper.terminal_ingest(rows)
    if manifest['status']!='finished' or errors or len(rows)!=39 or [latest.get(r['request_id'],{}).get('status') for r in schedule]!=['ok']*38+['failed']+[None]*4:raise ValueError('Actual 38 successes, failed request 39 and four unattempted required')
    http=b.core.read_rows(PREVIOUS/'http-receipts.jsonl')
    if len(http)!=39 or any((r['request_id'],r['hash'],r['status'],r['matches_schedule'])!=(s['request_id'],s['hash'],200 if i<38 else 503,True) for i,(r,s) in enumerate(zip(http,schedule))):raise ValueError('Actual successful original HTTP evidence required')
    user=p['namespace']+':locomo:conv-43';db=Path(p['data_dir'])/sha(user.encode())/'memory.sqlite';receipts=receipt_state(db,schedule,p['namespace'],38)
    paths=[PREVIOUS/n for n in ['plan.json','runtime.json','service.json','http-receipts.jsonl']]+[run/'manifest.json',run/'ingest.jsonl']
    pins.update({str(f):sha(f.read_bytes()) for f in paths})
    snapshot={'source':str(db),'source_sha256':sha(db.read_bytes()),'logical_state':fixed.logical_state(db),'revision':38}
    return p,schedule,receipts,snapshot,pins


def bind(candidate):
    b.HERE=HERE;b.FROZEN=FROZEN;b.CAMPAIGN=CAMPAIGN;b.RESUME_NAMESPACE=read(PREVIOUS/'plan.json')['namespace'];b.SCOPE=SCOPE
    b.CANDIDATE=Path(candidate['root']);b.COMMIT=candidate['commit'];b.MANIFEST_SHA=candidate['manifest_sha256']


def prepare(args):
    campaign=ROOT/'artifacts'/tail.name(args.campaign)
    if campaign.exists():raise FileExistsError('Preserve existing campaign')
    _,schedule,receipts,snapshot,origin_files=source()
    if not args.candidate_root or not args.candidate_commit or not args.candidate_manifest_sha256:raise ValueError('A frozen batching candidate root, commit and manifest hash are required')
    candidate={'root':str(Path(args.candidate_root).resolve()),'commit':args.candidate_commit,'manifest_sha256':args.candidate_manifest_sha256};bind(candidate)
    b.source() # Verify every candidate byte before creating a campaign.
    b.validate=lambda *_:({},[])
    try:b.prepare(args)
    finally:b.validate=validate
    write(campaign/'candidate-binding.json',candidate);write(campaign/'schedule.json',schedule);write(campaign/'receipts.private.json',receipts)
    captured=campaign/'conv43-rev38.sqlite'
    with closing(sqlite3.connect(Path(snapshot['source']).as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(captured)) as dst:src.backup(dst)
    if fixed.logical_state(captured)!=snapshot['logical_state'] or receipt_state(captured,schedule,b.RESUME_NAMESPACE,38)!=receipts:raise ValueError('Captured prefix changed')
    snapshot.update(path=str(captured),sha256=sha(captured.read_bytes()))
    shutil.copy2(BASE,campaign/'executor/scripts/probes/run-conv43-base.py')
    p=read(campaign/'plan.json');p.update(recovery={'previous':str(PREVIOUS),'prefix_adds':38,'new_tail_adds':5,'snapshot':snapshot,'origin_files':origin_files},candidate_binding=candidate,base_entry_sha256=BASE_SHA,base_adaptation_sha256=sha(adapted_base(BASE.read_bytes()).encode()))
    for f in [campaign/'candidate-binding.json',captured,campaign/'schedule.json',campaign/'receipts.private.json',campaign/'executor/scripts/probes/run-conv43-base.py']:p['dependencies'][str(f)]=sha(f.read_bytes())
    write(campaign/'plan.json',p);validate(campaign);out={'validation':'passed','model_calls_made':0,'prefix_adds':38,'new_tail_adds':5,'planned_adds':43,'planned_questions':47,'candidate_commit':b.COMMIT,'entry_point':p['entry_point']};write(campaign/'validation.json',out);return out


def validate(campaign,fresh=True):
    p=read(campaign/'plan.json');binding=read(campaign/'candidate-binding.json');bind(binding)
    if p['candidate_binding']!=binding or p.get('base_entry_sha256')!=BASE_SHA or p.get('base_adaptation_sha256')!=sha(adapted_base(BASE.read_bytes()).encode()):raise ValueError('Candidate or exact base adaptation changed')
    result=BASE_VALIDATE(campaign,fresh);_,schedule,receipts,snapshot,pins=source();r=p['recovery'];captured=r['snapshot']
    if {k:r.get(k) for k in ['previous','prefix_adds','new_tail_adds','origin_files']}!={'previous':str(PREVIOUS),'prefix_adds':38,'new_tail_adds':5,'origin_files':pins} or any(captured.get(k)!=v for k,v in snapshot.items()):raise ValueError('Recovery provenance changed')
    if captured['path']!=str(campaign/'conv43-rev38.sqlite') or sha(Path(captured['path']).read_bytes())!=captured['sha256'] or fixed.logical_state(captured['path'])!=snapshot['logical_state'] or receipt_state(captured['path'],schedule,p['namespace'],38)!=receipts:raise ValueError('Frozen snapshot changed')
    if read(campaign/'receipts.private.json')!=receipts or read(campaign/'schedule.json')!=schedule:raise ValueError('Recovery prefix or schedule changed')
    for f in [campaign/'candidate-binding.json',Path(captured['path']),campaign/'executor/scripts/probes/run-conv43-base.py']:
        if p['dependencies'].get(str(f))!=sha(f.read_bytes()):raise ValueError('Recovery dependency is not pinned')
    return result


def restore(campaign):
    p=read(campaign/'plan.json');s=p['recovery']['snapshot'];user=p['namespace']+':locomo:conv-43';db=Path(p['data_dir'])/sha(user.encode())/'memory.sqlite'
    if db.parent.exists():raise FileExistsError('Never reuse an execution clone')
    db.parent.mkdir();shutil.copy2(s['path'],db)
    if sha(db.read_bytes())!=s['sha256'] or receipt_state(db,read(campaign/'schedule.json'),p['namespace'],38)!=read(campaign/'receipts.private.json') or fixed.logical_state(db)!=s['logical_state']:raise ValueError('Restored prefix must match before service starts')


def storage_check(plan,campaign):
    result=BASE_STORAGE(plan,campaign);errors=result['reasons'];schedule=read(campaign/'schedule.json');prefix={r['request_id'] for r in schedule[:38]};remaining={r['request_id'] for r in schedule[38:]}
    latest,_=b.helper.terminal_ingest(b.core.read_rows(Path(plan['phases'][0]['run_dir'])/'ingest.jsonl'));success={rid for rid,r in latest.items() if r.get('status')=='ok'};new=success&remaining
    # The restored prefix also remains committed after an early launch/transport failure.
    errors[:]=[e for e in errors if e!='revision_or_receipt_set_mismatch']
    user=plan['namespace']+':locomo:conv-43';db=Path(plan['data_dir'])/sha(user.encode())/'memory.sqlite';s=plan['recovery']['snapshot']
    if db.exists():
        try:receipt_state(db,schedule,plan['namespace'],38+len(new))
        except ValueError:errors.append('restored_prefix_or_successful_tail_receipt_mismatch')
        if new!={r['request_id'] for r in schedule[38:38+len(new)]}:errors.append('successful_tail_not_contiguous')
        if not new and fixed.logical_state(db)!=s['logical_state']:errors.append('no_successful_tail_but_clone_changed')
    elif success:errors.append('successful_HTTP_without_database')
    for row in b.core.read_rows(campaign/'http-receipts.jsonl'):
        if row['request_id'] in prefix and (row.get('classification')!='prefix' or row.get('status')==200 and not row.get('matches_snapshot')):errors.append('prefix_HTTP_receipt_not_identical')
    traces=b.core.read_rows(campaign/'model-trace.jsonl');new_trace_times=[r.get('at','') for r in traces if r.get('identity',{}).get('request_id') in remaining]
    if any(r.get('identity',{}).get('request_id') in prefix for r in traces):errors.append('prefix_called_generation_model')
    for call in b.core.read_rows(campaign/'model-calls.jsonl'):
        if call.get('kind')=='embedding' and call.get('action')=='add' and (not new_trace_times or call.get('at','')<min(new_trace_times)):errors.append('add_embedding_before_new_tail')
    if sha(Path(s['source']).read_bytes())!=s['source_sha256'] or fixed.logical_state(s['source'])!=s['logical_state'] or sha(Path(s['path']).read_bytes())!=s['sha256']:errors.append('source_or_snapshot_changed')
    result.update(integrity='fail' if errors else 'pass',prefix_adds=38,new_successful_tail_adds=len(new),new_tail_adds=5,source_unchanged='source_or_snapshot_changed' not in errors);return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--campaign',default=CAMPAIGN);parser.add_argument('--port',type=int,default=8125);parser.add_argument('--candidate-root');parser.add_argument('--candidate-commit');parser.add_argument('--candidate-manifest-sha256');parser.add_argument('--detach',action='store_true');actions=parser.add_mutually_exclusive_group(required=True)
    for name in ['plan','validate','run','status']:actions.add_argument('--'+name,action='store_true')
    args=parser.parse_args();os.umask(0o077);campaign=ROOT/'artifacts'/tail.name(args.campaign)
    if args.detach and not args.run:parser.error('--detach requires --run')
    if args.plan:print(json.dumps(prepare(args),indent=2));return
    p=read(campaign/'plan.json');entry=Path(p['entry_point'])
    if HERE!=entry:os.execv(sys.executable,[sys.executable,str(entry),'--campaign',args.campaign,'--status' if args.status else '--validate' if args.validate else '--run',*(['--detach'] if args.detach else [])])
    bind(read(campaign/'candidate-binding.json'));b.validate=validate;b.storage_check=storage_check
    class RestoredRun(b.core.ManagedRun):
        def spawn(self,role,*args,**kwargs):
            if role=='service':restore(campaign)
            return super().spawn(role,*args,**kwargs)
    b.core.ManagedRun=RestoredRun
    sys.argv=[str(HERE),'--campaign',args.campaign,'--status' if args.status else '--validate' if args.validate else '--run',*(['--detach'] if args.detach else [])];b.main()

if __name__=='__main__':
    try:main()
    except b.core.RunInterrupted as error:sys.exit(128+error.signum)
