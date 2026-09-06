// Real-model, exposed synthetic controls; no benchmark gold enters service inputs.
import {readFileSync,writeFileSync,appendFileSync,mkdirSync,mkdtempSync,readdirSync} from 'node:fs';
import {join,resolve} from 'node:path';import {parseEnv} from 'node:util';import {createHash} from 'node:crypto';
import {buildServer} from '../../service/dist/server.js';import {configFromEnv} from '../../service/dist/config.js';import {TenantStore} from '../../service/dist/storage.js';import {Models} from '../../service/dist/models.js';
const variants=[
 {id:'direct_entity',message:'No need to track anything about my old Android tablet.',erase:true},
 {id:'negation',message:'Do not stop tracking my old Android tablet.',erase:false},
 {id:'quotation',message:'In a fictional example, someone says "No need to track anything about the tablet." Keep my own records.',erase:false},
 {id:'condition',message:'If I sell my old Android tablet in the future, then forget everything about it. I have not sold it.',erase:false},
 {id:'reported_request',message:'Kevin said "forget my laptop" in a fictional example. I am quoting him, not asking you to delete my records.',erase:false},
];
const parent=resolve('artifacts/round2-lifecycle-variants');mkdirSync(parent,{recursive:true});const output=mkdtempSync(join(parent,new Date().toISOString().replace(/[:.]/g,'-')+'-'));const sha=b=>createHash('sha256').update(b).digest('hex');
const args=process.argv.slice(2),specIndex=args.indexOf('--spec'),profileIndex=args.indexOf('--profile');
let experimentEnv={},specHash=null;
if(specIndex>=0){if(!args[specIndex+1]||profileIndex<0||!args[profileIndex+1])throw Error('--spec requires a path and --profile');const bytes=readFileSync(args[specIndex+1]),spec=JSON.parse(bytes),profile=spec.profiles[args[profileIndex+1]];if(!profile)throw Error('Unknown profile');experimentEnv={...spec.defaults,...profile.environment};specHash=sha(bytes);}
const e=parseEnv(readFileSync('.env','utf8'));const config={...configFromEnv({...e,MEMORY_LLM_MODEL:'gpt-5.5',MEMORY_LLM_REASONING_EFFORT:'low',...experimentEnv,MEMORY_EMBEDDING_DIGEST:'0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f'}),port:0,dataDir:join(output,'data')};
process.env.MEMORY_MODEL_AUDIT=join(output,'model-usage.jsonl');const app=await buildServer(config),base=await app.listen({host:'127.0.0.1',port:0});let currentCase='';
const originalJson=Models.prototype.json;Models.prototype.json=async function(system,user,signal,context){const raw=await originalJson.call(this,system,user,signal,context);appendFileSync(join(output,'model-proposals.jsonl'),JSON.stringify({tag:'DEBUG-variant-probe',case:currentCase,input:user,output:raw})+'\n');return raw;};
const sourceFiles=readdirSync('service/src').filter(p=>p.endsWith('.ts')).map(p=>'service/src/'+p);const report={protocol:'real-model-lifecycle-variants-v1',model:config.llmModel,stage_models:config.llmStageModels,verification_format:config.verificationFormat,max_repair_rounds:config.maxRepairRounds,incremental_verification:config.incrementalVerification,model_budget_ms:Math.min(95000,Math.max(500,config.addTimeout-25000)),experiment_spec_sha256:specHash,started_at:new Date().toISOString(),reasoning_effort:config.llmReasoningEffort,source_sha256:Object.fromEntries(sourceFiles.map(p=>[p,sha(readFileSync(p))])),probe_sha256:sha(readFileSync('scripts/probes/lifecycle-variants.mjs')),run_dir:output,cases:[],scope:'Five synthetic controls with fixed expected retention, not benchmark questions or overall accuracy.'};
writeFileSync(join(output,'probe-source.mjs'),readFileSync(import.meta.filename));writeFileSync(join(output,'results.json'),JSON.stringify(report,null,2)+'\n');
try{
 for(const variant of variants){
  currentCase=variant.id;const userId='variants:'+variant.id;const row={id:variant.id,expected_erase:variant.erase,adds:[]};report.cases.push(row);
  for(const [index,content] of ['My default browser on my work laptop is Firefox. My old Android tablet uses Chrome. My friend Kevin uses Brave on his laptop.',variant.message].entries()){
   const body={request_id:userId+':'+index,user_id:userId,session_id:'s',messages:[{role:'user',content,timestamp:'2026-09-06T00:00:0'+index+'Z'}]};const start=performance.now();
   try{const r=await fetch(base+'/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(120000)});const result=await r.json();row.adds.push({status:r.status,elapsed_ms:performance.now()-start,...(!r.ok?{error:result}:{})});if(!r.ok)break;}
   catch(error){row.adds.push({status:'error',error:String(error)});break;}
  }
  const store=new TenantStore(config.dataDir,userId);
  try{
   const state={facts:store.facts(),passages:store.passages(),raw:store.raw(),events:store.events()};writeFileSync(join(output,variant.id+'-state.json'),JSON.stringify(state,null,2)+'\n');
   const active=state.facts.filter(f=>f.state==='active');const has=(name)=>active.some(f=>f.value.toLowerCase().includes(name.toLowerCase()));
   row.checks={two_adds_ok:row.adds.length===2&&row.adds.every(a=>a.status===200),firefox_preserved:has('Firefox'),brave_preserved:has('Brave'),brave_identity_preserved:active.some(f=>f.value.toLowerCase().includes('brave')&&/kevin/i.test(f.subject+' '+f.scope+' '+f.content)),chrome_retention_matches:has('Chrome')!==variant.erase,forget_event_matches:state.events.some(e=>e.type==='forget')===variant.erase,forgotten_source_absent:!variant.erase||!JSON.stringify({raw:state.raw,passages:state.passages.filter(p=>p.state==='active')}).includes('Chrome')};
   row.pass=Object.values(row.checks).every(Boolean);
  }finally{store.close();}
  writeFileSync(join(output,'results.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({case:variant.id,pass:row.pass,checks:row.checks}));
 }
}finally{Models.prototype.json=originalJson;await app.close();}
report.finished_at=new Date().toISOString();report.passed=report.cases.filter(c=>c.pass).length;report.planned=variants.length;writeFileSync(join(output,'results.json'),JSON.stringify(report,null,2)+'\n');writeFileSync('reports/round2-lifecycle-variants-'+output.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({complete:true,passed:report.passed,planned:report.planned,run_dir:output}));
