// Real fresh-tenant control. No stored successful generations are replayed.
import {readFileSync,writeFileSync,mkdtempSync,mkdirSync,copyFileSync,readdirSync} from 'node:fs';import {resolve,join} from 'node:path';import {parseEnv} from 'node:util';import {createHash} from 'node:crypto';
import {configFromEnv} from '../../service/dist/config.js';import {buildServer} from '../../service/dist/server.js';import {TenantStore} from '../../service/dist/storage.js';import {extractionShards} from '../../service/dist/extraction-shards.js';
const sha=x=>createHash('sha256').update(x).digest('hex'),dir=mkdtempSync(resolve('artifacts/round2-extraction-shards-http-')),specPath='configs/round2-source-first-parallel.json',spec=JSON.parse(readFileSync(specPath));
const config={...configFromEnv({...parseEnv(readFileSync('.env','utf8')),...spec.defaults}),dataDir:join(dir,'data'),port:0};
process.env.MEMORY_MODEL_TRACE=join(dir,'private-model-trace.jsonl');process.env.MEMORY_MODEL_AUDIT=join(dir,'model-calls.jsonl');
const timestamp='2026-01-01T00:00:00Z',user_id='parallel-http',session_id='s',req={user_id,session_id,request_id:'same-request-forget',messages:['My access code is 9182.','My brother uses the access code 9182.','I use Firefox.','Forget my access code 9182.'].map(content=>({role:'user',content,timestamp}))};
writeFileSync(join(dir,'request.json'),JSON.stringify(req,null,2)+'\n',{mode:0o600});copyFileSync(import.meta.filename,join(dir,'probe-source.mjs'));copyFileSync(specPath,join(dir,'spec.json'));mkdirSync(join(dir,'source-snapshot'));const files=readdirSync('service/src').filter(f=>f.endsWith('.ts'));for(const f of files)copyFileSync('service/src/'+f,join(dir,'source-snapshot',f));
const report={protocol:'extraction-shards-http-v1',run_dir:dir,scope:'One synthetic fresh v10 HTTP request with four human messages. Real routing, three extraction shards, global verification, erasure and local embeddings. Same-request creation and deletion across shards; preserve another actor with the same value. This is a control, not benchmark accuracy or a full repeat.',source_sha256:Object.fromEntries(files.map(f=>[f,sha(readFileSync('service/src/'+f))])),spec_sha256:sha(readFileSync(specPath)),request_sha256:sha(readFileSync(join(dir,'request.json'))),shards:extractionShards(req,3),started_at:new Date().toISOString(),status:'running',checks:[]};
const save=()=>writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');save();console.log(JSON.stringify({run_dir:dir,status:'running'}));const app=await buildServer(config);await app.listen({port:0,host:'127.0.0.1'});const base=app.listeningOrigin;
try{
 const start=performance.now(),r=await fetch(base+'/add',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(req),signal:AbortSignal.timeout(125000)}),body=await r.json();report.add={status:r.status,elapsed_ms:performance.now()-start,body};save();if(r.status!==200)throw Error('Add failed');
 const searchResponse=await fetch(base+'/search',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({user_id,query:'Which browser do I use, and whose access code is 9182?',top_k:20}),signal:AbortSignal.timeout(60000)}),search=await searchResponse.json();writeFileSync(join(dir,'search.json'),JSON.stringify(search)+'\n');if(searchResponse.status!==200)throw Error('Search failed');
 const again=await fetch(base+'/add',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(req),signal:AbortSignal.timeout(125000)});report.repeated_add_status=again.status;await again.text();
 await app.close();const store=new TenantStore(config.dataDir,user_id);try{
  const snapshot=store.snapshot(session_id),facts=store.facts(),active=facts.filter(f=>f.state==='active'),sources=snapshot.erasureSources??[],passages=store.passages(),calls=readFileSync(join(dir,'model-calls.jsonl'),'utf8').trim().split('\n').map(JSON.parse),trace=readFileSync(join(dir,'private-model-trace.jsonl'),'utf8').trim().split('\n').map(JSON.parse),extractions=calls.filter(c=>c.purpose==='extraction'&&c.outcome==='ok');
  const state={revision:store.revision(),source_format:store.meta('source_format'),facts:facts.map(({vector,...f})=>f),sources,passages:passages.map(({vector,...p})=>p)};writeFileSync(join(dir,'state.json'),JSON.stringify(state)+'\n');
  report.model_calls=calls.map(({at,purpose,outcome,elapsed_ms,extraction_shard,usage})=>({at,purpose,outcome,elapsed_ms,extraction_shard,usage}));
  report.checks=[
   ['fresh_v10_single_commit',state.source_format==='dual-source-v10'&&state.revision===1],
   ['three_real_extraction_shards',extractions.length===3&&new Set(extractions.map(x=>x.extraction_shard?.index)).size===3],
   ['creation_and_deletion_owned_by_different_shards',!report.shards.some(s=>s.participants.includes(0)&&s.participants.includes(3))],
   ['global_verification_exercised',trace.some(t=>t.purpose==='verification'&&t.outcome==='ok')],
   ['user_code_facts_erased',facts.some(f=>f.state==='erased'&&f.subject==='user')&&!active.some(f=>f.subject==='user'&&/9182/.test(f.content+' '+f.value))],
   ['user_assertion_and_command_redacted',!JSON.stringify(sources).includes('My access code is 9182')&&!JSON.stringify(sources).includes('Forget my access code 9182')],
   ['brother_same_value_preserved',active.some(f=>/brother/i.test(f.subject)&&/9182/.test(f.content+' '+f.value))],
   ['browser_preserved',active.some(f=>/Firefox/.test(f.value))],
   ['search_preserves_browser_and_brother',/Firefox/.test(JSON.stringify(search))&&/brother/i.test(JSON.stringify(search))&&/9182/.test(JSON.stringify(search))],
   ['idempotent_retry_no_extra_commit_or_extraction',again.status===200&&state.revision===1&&extractions.length===3],
  ].map(([name,pass])=>({name,pass}));
 }finally{store.close();}report.status=report.checks.every(c=>c.pass)?'passed':'failed_checks';
}catch(e){report.status='failed';report.error={message:e.message};}finally{await app.close();report.finished_at=new Date().toISOString();save();writeFileSync('reports/'+dir.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({status:report.status,run_dir:dir,add:report.add,checks:report.checks,error:report.error}));}
