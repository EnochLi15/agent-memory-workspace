// Read-only logical snapshots after terminal sample failures in a live campaign.
import {readFileSync,writeFileSync,mkdtempSync} from 'node:fs';import {resolve,join} from 'node:path';import {createHash} from 'node:crypto';import {createRequire} from 'node:module';import assert from 'node:assert/strict';
const require=createRequire(new URL('../../service/package.json',import.meta.url)),Database=require('better-sqlite3');
const parent=resolve(process.argv[2]),sha=x=>createHash('sha256').update(x).digest('hex'),lines=p=>readFileSync(p,'utf8').trim().split('\n').filter(Boolean).map(JSON.parse);
const manifestBytes=readFileSync(join(parent,'manifest.json')),manifest=JSON.parse(manifestBytes),requests=lines(join(parent,'requests.jsonl')),ingest=lines(join(parent,'ingest.jsonl')),judgments=lines(join(parent,'judgments.jsonl'));
const failures=ingest.filter(x=>x.status==='failed');assert.ok(failures.length);const dir=mkdtempSync(resolve('artifacts/round2-development-snapshots-'));
const report={protocol:'failed-development-snapshots-v1',run_dir:dir,parent_run:parent,parent_manifest_sha256:sha(manifestBytes),source_commit:manifest.service_commit,eval_commit:manifest.eval_commit,captured_at:new Date().toISOString(),cases:[],scope:'Logical SQLite snapshots using read-only connections and a read transaction, after failed sample judgment rows exist. Other campaign samples may still run. No repair, commit or replacement of original evaluation results.'};
for(const failure of failures){
 const request=requests.find(x=>x.path==='/add'&&x.body.request_id===failure.request_id).body;
 const sample=manifest.samples.find(s=>request.user_id.endsWith(':'+s));assert.ok(sample);assert.ok(judgments.some(j=>j.sample_id===sample&&j.status==='service_error'));
 const last=ingest.filter(x=>x.request_id.startsWith(request.user_id+':')).at(-1);assert.equal(last.request_id,failure.request_id);assert.equal(last.status,'failed');
 const file=join(manifest.service_configuration.dataDir,sha(request.user_id),'memory.sqlite'),db=new Database(file,{readonly:true,fileMustExist:true});let snapshot;
 try{snapshot=db.transaction(()=>{
  assert.equal(db.prepare('SELECT value FROM meta WHERE key=?').get('user_id').value,request.user_id);
  assert.equal(db.prepare('SELECT id FROM requests WHERE id=?').get(request.request_id),undefined,'Failed request unexpectedly committed');
  const format=db.prepare('SELECT value FROM meta WHERE key=?').get('source_format')?.value;
  return {revision:Number(db.prepare('SELECT value FROM meta WHERE key=?').get('revision').value),facts:db.prepare('SELECT body FROM facts').all().map(r=>JSON.parse(r.body)),tail:db.prepare('SELECT body FROM messages WHERE session_id=? ORDER BY ordinal DESC LIMIT 10').all(request.session_id).reverse().map(r=>JSON.parse(r.body)),anchor:db.prepare('SELECT anchor FROM sessions WHERE id=?').get(request.session_id)?.anchor??null,...(format?.endsWith('-v3')?{erasureBoundaries:db.prepare('SELECT body FROM markers').all().map(r=>JSON.parse(r.body))}:{})};
 })();}finally{db.close();}
 const successful=new Set(ingest.filter(x=>x.request_id.startsWith(request.user_id+':')&&x.status==='ok').map(x=>x.request_id));assert.equal(snapshot.revision,successful.size);
 const provenance={sample,parent_run:parent,parent_manifest_sha256:sha(manifestBytes),source_commit:manifest.service_commit,eval_commit:manifest.eval_commit,service_config_sha256:manifest.service_config_sha256,failed_add:failure};
 const target=join(dir,sample+'.json');writeFileSync(target,JSON.stringify({protocol:'failed-development-chunk-snapshot-v1',request,snapshot,provenance})+'\n');
 report.cases.push({sample,request_id:request.request_id,snapshot_revision:snapshot.revision,fact_count:snapshot.facts.length,file:target,sha256:sha(readFileSync(target)),original_error:failure.error});
}
writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');writeFileSync('reports/round2-development-snapshots-'+dir.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
