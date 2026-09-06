import {readFileSync,writeFileSync,mkdtempSync,appendFileSync,readdirSync} from 'node:fs';
import {join,resolve} from 'node:path';import {parseEnv} from 'node:util';import {createHash} from 'node:crypto';
import {buildServer} from '../../service/dist/server.js';import {configFromEnv} from '../../service/dist/config.js';import {TenantStore} from '../../service/dist/storage.js';import {Models} from '../../service/dist/models.js';
const dir=mkdtempSync(join(resolve('artifacts'),'round2-binding-lifecycle-'));const sha=x=>createHash('sha256').update(x).digest('hex');
const env=parseEnv(readFileSync('.env','utf8'));const config={...configFromEnv({...env,MEMORY_MODE:'enhanced',MEMORY_LLM_MODEL:'gpt-5.5',MEMORY_LLM_REASONING_EFFORT:'low',MEMORY_EMBEDDING_DIGEST:'0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f'}),dataDir:join(dir,'data'),port:0};
process.env.MEMORY_MODEL_AUDIT=join(dir,'model-usage.jsonl');const original=Models.prototype.json;
Models.prototype.json=async function(system,user,signal){const output=await original.call(this,system,user,signal);appendFileSync(join(dir,'model-proposals.jsonl'),JSON.stringify({input:user,output})+'\n');return output;};
const app=await buildServer(config),base=await app.listen({host:'127.0.0.1',port:0});
const report={protocol:'real-model-property-versus-value-forget-v1',model:config.llmModel,reasoning_effort:config.llmReasoningEffort,source_sha256:Object.fromEntries(readdirSync('service/src').filter(p=>p.endsWith('.ts')).map(p=>['service/src/'+p,sha(readFileSync('service/src/'+p))])),probe_sha256:sha(readFileSync('scripts/probes/binding-lifecycle.mjs')),run_dir:dir,cases:[],scope:'Two fixed lifecycle controls with actual model, local embedding, HTTP writes and searches. Not benchmark accuracy.'};
try{
 for(const boundary of ['property','value']){
  const user_id='binding:'+boundary,row={boundary,adds:[],searches:[]};report.cases.push(row);
  const statements=['I live in Seattle.','I now live in Boston.',boundary==='property'?'Forget every city I have lived in, including all older city records.':'Forget Boston, but keep the rest of my city history.'];
  for(const [i,content] of statements.entries()){
   const start=performance.now();const r=await fetch(base+'/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:user_id+':'+i,user_id,session_id:'s',messages:[{role:'user',content,timestamp:`2026-01-0${i+1}T00:00:00Z`}]}),signal:AbortSignal.timeout(120000)});const body=await r.json();row.adds.push({status:r.status,elapsed_ms:performance.now()-start,...(!r.ok?{error:body}:{})});if(!r.ok)break;
  }
  for(const query of ['What is my current city?','What cities did I live in previously?','What did I ask you to forget about my city records?']){
   const r=await fetch(base+'/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id,query,top_k:100}),signal:AbortSignal.timeout(60000)});row.searches.push({query,status:r.status,response:await r.json()});
  }
  const store=new TenantStore(config.dataDir,user_id);try{
   const state={facts:store.facts(),passages:store.passages(),raw:store.raw(),events:store.events()};writeFileSync(join(dir,boundary+'-state.json'),JSON.stringify(state,null,2)+'\n');
   const returned=JSON.stringify(row.searches.map(q=>q.response));
   row.checks={all_adds_ok:row.adds.length===3&&row.adds.every(r=>r.status===200),all_searches_ok:row.searches.every(r=>r.status===200),boston_not_returned:!returned.includes('Boston'),seattle_boundary:boundary==='property'?!returned.includes('Seattle'):JSON.stringify(row.searches[1].response).includes('Seattle'),no_erased_value_in_raw:!(boundary==='property'?['Boston','Seattle']:['Boston']).some(value=>JSON.stringify({raw:state.raw,passages:state.passages.filter(p=>p.state==='active')}).includes(value))};
   row.pass=Object.values(row.checks).every(Boolean);row.state_sha256=sha(readFileSync(join(dir,boundary+'-state.json')));
  }finally{store.close();}
  writeFileSync(join(dir,'results.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({boundary,pass:row.pass,checks:row.checks}));
 }
}finally{Models.prototype.json=original;await app.close();}
report.passed=report.cases.filter(c=>c.pass).length;report.planned=2;writeFileSync(join(dir,'results.json'),JSON.stringify(report,null,2)+'\n');writeFileSync('reports/round2-binding-lifecycle-live.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({complete:true,passed:report.passed,run_dir:dir}));
