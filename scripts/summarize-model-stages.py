"""Join private trace identities to usage audit, exporting only stage aggregates."""
import argparse, collections, datetime, hashlib, json, math, pathlib
p=argparse.ArgumentParser();p.add_argument('--run',type=pathlib.Path,required=True);p.add_argument('--audit',type=pathlib.Path,required=True);p.add_argument('--trace',type=pathlib.Path,required=True);p.add_argument('--output',type=pathlib.Path,required=True);a=p.parse_args()
def rows(path):
 result=[]
 for line in path.read_text().splitlines():
  if line.strip():
   try:result.append(json.loads(line))
   except json.JSONDecodeError:raise SystemExit('Incomplete JSONL row; retry the same running file later')
 return result
def percentile(xs,q):
 return sorted(xs)[max(0,math.ceil(len(xs)*q)-1)] if xs else None
manifest=json.loads((a.run/'manifest.json').read_text());ingest=rows(a.run/'ingest.jsonl');audit=rows(a.audit);trace=rows(a.trace)
identities={}
for r in trace:
 identity=r.get('identity');key=r['trace_id']
 if identity:
  if key in identities and identities[key]!=identity:raise SystemExit('Trace identity changed for one logical call')
  identities[key]=identity
stages=collections.defaultdict(list);requests=collections.defaultdict(list);unlinked=0
for r in audit:
 if r.get('kind')!='generation':continue
 stages[r.get('purpose','unknown')].append(r)
 identity=identities.get(r.get('trace_id'))
 if identity:requests[identity['request_id']].append(r)
 else:unlinked+=1
stage_summary={}
for purpose,rs in stages.items():
 times=[r['elapsed_ms'] for r in rs if isinstance(r.get('elapsed_ms'),(int,float))]
 stage_summary[purpose]={'attempts':len(rs),'outcomes':dict(collections.Counter(r.get('outcome','unknown') for r in rs)),'models':dict(collections.Counter(r.get('model','unknown') for r in rs)),'elapsed_ms':{'total':sum(times),'p50':percentile(times,.5),'p95':percentile(times,.95)},'reported_total_tokens':sum((r.get('usage') or {}).get('total_tokens',0) for r in rs),'attempts_without_reported_usage':sum(not r.get('usage') for r in rs)}
completed=[]
for r in ingest:
 if r['status'] not in ['ok','failed']:continue
 calls=requests.get(r['request_id'],[]);completed.append({'request_id':r['request_id'],'status':r['status'],'add_ms':r.get('elapsed_ms'),'generation_attempts':len(calls),'stage_attempts':dict(collections.Counter(x.get('purpose','unknown') for x in calls)),'generation_ms':sum(x.get('elapsed_ms',0) for x in calls),'error':r.get('error')})
report={'protocol':'model-stage-usage-review-v1','captured_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'run_id':manifest['run_id'],'run_status':manifest['status'],'service_commit':manifest['service_commit'],'input_sha256':{str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in [a.run/'manifest.json',a.run/'ingest.jsonl',a.audit,a.trace]},'ingest_status':dict(collections.Counter(x['status'] for x in ingest)),'stages':stage_summary,'unlinked_generation_attempts':unlinked,'completed_adds':completed,'scope':'Descriptive snapshot of recorded attempts; completed adds join private request identities without exporting inputs, outputs or credentials. Running audit and trace files are read sequentially; a newly completed call may be temporarily unlinked. Token totals include only reported usage, and errors with missing usage are not zero-cost. Concurrent diagnostic workload may affect observed latency; this is not an isolated performance comparison.'}
a.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'run_status':report['run_status'],'ingest_status':report['ingest_status'],'stages':{k:v['attempts'] for k,v in stage_summary.items()},'unlinked':unlinked}))
