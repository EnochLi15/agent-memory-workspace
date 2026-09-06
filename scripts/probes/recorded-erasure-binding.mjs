// One new semantic scope call on the exact archived proposal, then commit on an isolated diagnostic clone.
import {readFileSync,writeFileSync,appendFileSync,mkdtempSync,mkdirSync,copyFileSync,readdirSync} from 'node:fs';import {resolve,join} from 'node:path';import {parseEnv} from 'node:util';import {createHash} from 'node:crypto';import {createRequire} from 'node:module';import assert from 'node:assert/strict';
import {Models} from '../../service/dist/models.js';import {configFromEnv} from '../../service/dist/config.js';import {TenantStore} from '../../service/dist/storage.js';import {retrieve} from '../../service/dist/retrieval.js';import {erasureWork,decodeErasure,validateErasurePlan,ERASURE_PROMPT} from '../../service/dist/erasure.js';
const sha=x=>createHash('sha256').update(x).digest('hex'),read=p=>JSON.parse(readFileSync(p,'utf8')),require=createRequire(new URL('../../service/package.json',import.meta.url)),Database=require('better-sqlite3');
const snapshotPath=resolve(process.argv[2]),preparedDir=resolve(process.argv[3]),saved=read(snapshotPath),parent=read(join(preparedDir,'report.json')),prepared=read(join(preparedDir,'prepared.json'));
const args=process.argv.slice(4),reuseIndex=args.indexOf('--reuse'),reuseDir=reuseIndex>=0?resolve(args[reuseIndex+1]):null,reusePrepared=args.includes('--reuse-prepared-plan');
if(reuseDir&&reusePrepared)throw Error('Choose one replay mechanism');const queries=args.filter((x,i)=>x!=='--reuse-prepared-plan'&&i!==reuseIndex&&(reuseIndex<0||i!==reuseIndex+1));
assert.equal(parent.status,'prepared');assert.equal(parent.captured_snapshot_sha256,sha(readFileSync(snapshotPath)));assert.equal(parent.prepared_sha256,sha(readFileSync(join(preparedDir,'prepared.json'))));
const spec=read('configs/round2-quality-erasure.json'),config=configFromEnv({...parseEnv(readFileSync('.env','utf8')),...spec.defaults,...spec.profiles.facts.environment});
const dir=mkdtempSync(resolve('artifacts/round2-recorded-erasure-'));copyFileSync(import.meta.filename,join(dir,'probe-source.mjs'));process.env.MEMORY_MODEL_AUDIT=join(dir,'model-usage.jsonl');
const work=erasureWork(saved.request,saved.snapshot.facts,prepared.facts,prepared.operations,saved.snapshot.erasureBoundaries??[]),input=JSON.stringify({NEW_MESSAGES:saved.request.messages,CONTEXT_ONLY:saved.snapshot.tail,CANDIDATES:work.candidates.map((c,index)=>({index,...c}))});writeFileSync(join(dir,'input.json'),input+'\n');
const report={protocol:'recorded-erasure-scope-and-clone-v1',run_dir:dir,sample:saved.provenance.sample,snapshot_sha256:sha(readFileSync(snapshotPath)),parent_prepared_sha256:parent.prepared_sha256,spec_sha256:sha(readFileSync('configs/round2-quality-erasure.json')),source_sha256:Object.fromEntries(readdirSync('service/src').filter(p=>p.endsWith('.ts')).map(p=>[p,sha(readFileSync('service/src/'+p))])),input_sha256:sha(input),candidate_pairs:work.candidates.length,automatic_pairs:work.automatic.length,status:'running',scope:'The old archived extraction and semantic checks are fixed. Only erasure binding calls a new model. A private unerased v2 checkpoint may be transplanted to v3 for this transaction diagnostic, explicitly logged; this is not supported production migration, a fresh full prefix, an overall latency comparison or a benchmark score.'};
writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');const start=performance.now();
try{
 let plan;
 if(reusePrepared){assert.equal(parent.source_sha256['erasure.ts'],report.source_sha256['erasure.ts']);validateErasurePlan(prepared.erasurePlan,work);plan=prepared.erasurePlan;report.reuse={type:'prepared-plan',parent_report_sha256:sha(readFileSync(join(preparedDir,'report.json')))};report.new_generation_calls=0;}
 else{
  let raw;
  if(reuseDir){const reused=read(join(reuseDir,'report.json'));assert.equal(reused.input_sha256,sha(input));assert.equal(reused.source_sha256['erasure.ts'],report.source_sha256['erasure.ts']);raw=read(join(reuseDir,'output.json'));report.reuse={type:'fixed-output',parent_report_sha256:sha(readFileSync(join(reuseDir,'report.json'))),output_sha256:sha(readFileSync(join(reuseDir,'output.json')))};report.new_generation_calls=0;}
  else{raw=work.candidates.length?await new Models(config).json(ERASURE_PROMPT,input,AbortSignal.timeout(90000),{purpose:'erasure_binding'}):{decisions:[]};report.new_generation_calls=work.candidates.length?1:0;}
  writeFileSync(join(dir,'output.json'),JSON.stringify(raw)+'\n');plan=decodeErasure(raw,work);
 }
 report.binding_elapsed_ms=performance.now()-start;report.plan=plan;
 const manifest=read(join(saved.provenance.parent_run,'manifest.json'));assert.equal(manifest.service_config_sha256,saved.provenance.service_config_sha256);
 const tenantHash=sha(saved.request.user_id),destination=join(dir,'data',tenantHash);mkdirSync(destination,{recursive:true});const source=new Database(join(manifest.service_configuration.dataDir,tenantHash,'memory.sqlite'),{readonly:true,fileMustExist:true});
 try{await source.backup(join(destination,'memory.sqlite'));}finally{source.close();}
 const store=new TenantStore(join(dir,'data'),saved.request.user_id);
 try{
  assert.deepEqual(store.snapshot(saved.request.session_id),saved.snapshot);assert.equal(store.db.prepare('SELECT count(*) AS n FROM markers').get().n,0,'Transplant only allowed for a checkpoint with no erased-value markers');assert.ok(store.facts().every(f=>f.state!=='erased'));
  report.diagnostic_transplant={from:store.meta('source_format'),to:'dual-source-v3',erased_records:0,markers:0};assert.equal(store.meta('source_format'),'dual-source-v2');store.db.prepare("UPDATE meta SET value='dual-source-v3' WHERE key='source_format'").run();
  const next={...prepared,sourceFormat:'dual-source-v3',erasurePlan:plan};writeFileSync(join(dir,'prepared-with-plan.json'),JSON.stringify(next)+'\n');
  report.receipt=store.commit(saved.request,sha(JSON.stringify(saved.request)),next,saved.snapshot.revision);report.status='committed';
  const state={revision:store.revision(),facts:store.facts(),passages:store.passages(),events:store.events(),raw:store.raw()};writeFileSync(join(dir,'state.json'),JSON.stringify(state)+'\n');report.state_sha256=sha(readFileSync(join(dir,'state.json')));report.committed_revision=store.revision();
  const results=queries.map(query=>({query,memories:retrieve(store,{query,user_id:saved.request.user_id,top_k:100},null,{...config,rawFallback:true,eventView:true}).data}));writeFileSync(join(dir,'queries.json'),JSON.stringify(results)+'\n');report.queries_sha256=sha(readFileSync(join(dir,'queries.json')));
 }finally{store.close();}
}catch(e){report.status='failed';report.error={code:e.code??null,message:e.message};}
report.elapsed_ms=performance.now()-start;writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');writeFileSync('reports/round2-recorded-erasure-'+dir.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({run_dir:dir,status:report.status,candidate_pairs:report.candidate_pairs,binding_elapsed_ms:report.binding_elapsed_ms,error:report.error}));
