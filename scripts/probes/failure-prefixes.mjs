// Controlled replay of the four already exposed failed /add prefixes.
// Diagnostic extraction outputs stay in this ignored artifact directory only.
import {readFileSync,writeFileSync,appendFileSync,mkdirSync,mkdtempSync,readdirSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {parseEnv} from 'node:util';
import {createHash} from 'node:crypto';
import {buildServer} from '../../service/dist/server.js';
import {configFromEnv} from '../../service/dist/config.js';
import {Models} from '../../service/dist/models.js';
import {TenantStore} from '../../service/dist/storage.js';
const sha=x=>createHash('sha256').update(x).digest('hex');
const lines=p=>readFileSync(p,'utf8').split('\n').filter(Boolean).map(l=>JSON.parse(l));
const prior='eval/artifacts/holdout-v2-U3-memops';
const args=process.argv.slice(2);const selectedCase=args[0]?.startsWith('--')?undefined:args[0];
const modelIndex=args.indexOf('--model');const modelOverride=modelIndex>=0?args[modelIndex+1]:undefined;
if(modelIndex>=0&&!modelOverride)throw Error('Missing --model value');
const failures=lines(join(prior,'ingest.jsonl')).filter(r=>r.status==='failed'&&(!selectedCase||r.request_id.split(':')[2]===selectedCase));
if(!failures.length)throw Error('No matching known failed prefix');
const requests=lines(join(prior,'requests.jsonl')).filter(r=>r.path==='/add').map(r=>r.body);
const parentDir=resolve('artifacts/round2-failure-prefixes');mkdirSync(parentDir,{recursive:true});
// mkdtemp reserves the directory atomically: parallel starts can share a millisecond.
const runDir=mkdtempSync(join(parentDir,new Date().toISOString().replace(/[:.]/g,'-')+'-'));
const env=parseEnv(readFileSync('.env','utf8'));
const config={...configFromEnv({...env,MEMORY_EMBEDDING_DIGEST:'0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f'}),...(modelOverride?{llmModel:modelOverride}:{}),dataDir:join(runDir,'data'),port:0};
process.env.MEMORY_MODEL_AUDIT=join(runDir,'model-usage.jsonl');
const originalJson=Models.prototype.json;
Models.prototype.json=async function(system,user,signal){
 const raw=await originalJson.call(this,system,user,signal);
 if(!system.startsWith('Rank evidence'))appendFileSync(join(runDir,'extraction-proposals.jsonl'),JSON.stringify({tag:'DEBUG-prefix-replay',input_sha256:sha(user),input:user,output:raw})+'\n');
 return raw;
};
const sourceFiles=readdirSync('service/src',{recursive:true}).filter(p=>p.endsWith('.ts')).map(p=>'service/src/'+p);
const report={protocol:'failed-prefix-http-replay-v1',run_dir:runDir,extraction_model:config.llmModel,embedding_model:config.embeddingModel,source_sha256:Object.fromEntries(sourceFiles.map(p=>[p,sha(readFileSync(p))])),probe_sha256:sha(readFileSync('scripts/probes/failure-prefixes.mjs')),prior_requests_sha256:sha(readFileSync(join(prior,'requests.jsonl'))),started_at:new Date().toISOString(),scope:'Replay all original messages and chunk boundaries through each of four known failing adds; namespace changed for isolation. Fresh real extraction/local embeddings. HTTP success alone does not prove lifecycle semantics or full-sample recovery.',cases:[]};
const app=await buildServer(config);const base=await app.listen({host:'127.0.0.1',port:0});
try{
 for(const failure of failures){
  const target=requests.find(r=>r.request_id===failure.request_id);if(!target)throw Error('Missing failed request');
  const originals=requests.filter(r=>r.user_id===target.user_id);const stop=originals.findIndex(r=>r.request_id===target.request_id);
  const prefix=[...new Map(originals.slice(0,stop+1).map(r=>[r.request_id,r])).values()];
  const userId='round2-prefix:'+target.user_id.split(':').at(-1);
  const row={user_id:userId,original_failure:failure,planned_adds:prefix.length,adds:[],status:'running'};report.cases.push(row);
  for(const [index,b] of prefix.entries()){
   const body={...b,user_id:userId,request_id:userId+':'+index};const start=performance.now();
   try{
    const response=await fetch(base+'/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(125000)});
    const result=await response.json();row.adds.push({original_request_id:b.request_id,status:response.status,elapsed_ms:performance.now()-start,...(!response.ok?{error:result}:{})});
    console.log(JSON.stringify({case:userId,index,status:response.status}));
    if(!response.ok){row.status='failed';break;}
   }catch(error){row.adds.push({original_request_id:b.request_id,status:'error',elapsed_ms:performance.now()-start,error:error instanceof Error?error.message:'Unknown error'});row.status='failed';break;}
   writeFileSync(join(runDir,'results.json'),JSON.stringify(report,null,2)+'\n');
  }
  if(row.status==='running')row.status='prefix_accepted';
  writeFileSync(join(runDir,'results.json'),JSON.stringify(report,null,2)+'\n');
 }
}finally{await app.close();Models.prototype.json=originalJson;}
for(const row of report.cases){
 const store=new TenantStore(config.dataDir,row.user_id);
 try{const snapshot={revision:store.revision(),facts:store.facts(),events:store.events(),passages:store.passages()};writeFileSync(join(runDir,row.user_id.split(':').at(-1)+'-state.json'),JSON.stringify(snapshot,null,2)+'\n');row.final_revision=snapshot.revision;row.fact_states=Object.fromEntries([...new Set(snapshot.facts.map(f=>f.state))].map(state=>[state,snapshot.facts.filter(f=>f.state===state).length]));}
 finally{store.close();}
}
report.finished_at=new Date().toISOString();report.prefixes_accepted=report.cases.filter(c=>c.status==='prefix_accepted').length;report.planned=failures.length;
writeFileSync(join(runDir,'results.json'),JSON.stringify(report,null,2)+'\n');writeFileSync('reports/round2-failure-prefixes-'+runDir.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({complete:true,prefixes_accepted:report.prefixes_accepted,planned:report.planned,run_dir:runDir}));
