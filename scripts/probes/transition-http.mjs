// Fixed, grounded extraction proposals isolate the observed storage failure.
// Verification, relation classification, embedding, HTTP storage and Answer are real.
import {readFileSync,writeFileSync,appendFileSync,mkdtempSync,readdirSync} from 'node:fs';import {resolve,join} from 'node:path';import {parseEnv} from 'node:util';import {createHash} from 'node:crypto';
import {buildServer} from '../../service/dist/server.js';import {Models} from '../../service/dist/models.js';import {configFromEnv} from '../../service/dist/config.js';import {TenantStore} from '../../service/dist/storage.js';
import {completion} from '../../eval/dist/models.js';import {ANSWER_PROMPT} from '../../eval/dist/runner.js';
const dir=mkdtempSync(resolve('artifacts/round2-transition-http-')),sha=x=>createHash('sha256').update(x).digest('hex'),env=parseEnv(readFileSync('.env','utf8'));
const spec=JSON.parse(readFileSync('configs/round2-quality-transitions.json')),config={...configFromEnv({...env,...spec.defaults}),port:0,dataDir:join(dir,'data')};
const detail='I drink black drip coffee every morning, no milk, no sugar.',routine='I like the routine of just a plain cup of coffee.',change='I now prefer a latte with whole milk instead of black drip coffee.';
const fact=(content,value)=>({content,value,subject:'user',predicate:'coffee_preference',scope:'',modality:'confirmed',cardinality:'single',sources:[{index:0,quote:content}]});
const steps=[{text:detail,facts:[fact(detail,'black drip coffee')]},{text:detail+' '+routine,facts:[fact(detail,'black drip coffee'),fact(routine,'plain coffee routine')]},{text:change,facts:[fact(change,'latte with whole milk')]}];
let step=0,injections=0;const original=Models.prototype.json;
process.env.MEMORY_MODEL_AUDIT=join(dir,'model-usage.jsonl');
Models.prototype.json=async function(system,input,signal,context){
 const replay=context?.purpose==='extraction';if(replay)injections++;
 appendFileSync(join(dir,'model-inputs.jsonl'),JSON.stringify({step,purpose:context?.purpose,origin:replay?'fixed_grounded_extraction':'live_model',input,prompt_sha256:sha(system)})+'\n');
 const output=replay?{facts:steps[step].facts,operations:[]}:await original.call(this,system,input,signal,context);
 appendFileSync(join(dir,'model-outputs.jsonl'),JSON.stringify({step,purpose:context?.purpose,output})+'\n');return output;
};
writeFileSync(join(dir,'probe-source.mjs'),readFileSync(import.meta.filename));
const report={protocol:'fixed-proposal-live-transition-http-v1',run_dir:dir,source_sha256:Object.fromEntries(readdirSync('service/src').filter(p=>p.endsWith('.ts')).map(p=>[p,sha(readFileSync('service/src/'+p))])),probe_sha256:sha(readFileSync(import.meta.filename)),answer_prompt_sha256:sha(ANSWER_PROMPT),answer_model:'gpt-5.4-mini',started_at:new Date().toISOString(),steps:[],scope:'Three fresh HTTP writes with predetermined grounded extraction facts to isolate implicit transitions. Real independent verification, transition model, local embedding and fixed Answer. Not a fresh-extraction benchmark or paired full-run score.'};
const app=await buildServer(config),base=await app.listen({host:'127.0.0.1',port:0});
try{
 for(step=0;step<steps.length;step++){
  const r={user_id:'u',request_id:'r'+step,session_id:'s',messages:[{role:'user',content:steps[step].text,timestamp:`2026-01-0${step+1}T00:00:00Z`}]},start=performance.now();
  const response=await fetch(base+'/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(r),signal:AbortSignal.timeout(125000)}),body=await response.json();
  report.steps.push({step,status:response.status,elapsed_ms:performance.now()-start,...(!response.ok?{error:body}:{})});if(!response.ok)break;
  const store=new TenantStore(config.dataDir,'u');let facts;try{facts=store.facts();writeFileSync(join(dir,`state-${step}.json`),JSON.stringify({revision:store.revision(),facts,passages:store.passages(),snapshot:store.snapshot('s')})+'\n');}finally{store.close();}
  if(step>0){
   const question='What is my usual coffee order, including milk and sugar preferences?';
   const search=await fetch(base+'/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:question,user_id:'u',top_k:32}),signal:AbortSignal.timeout(60000)}),memories=await search.json();if(!search.ok)throw Error('Search failed');
   const answer=await completion(env.MEMORY_LLM_BASE_URL,env.MEMORY_LLM_API_KEY,report.answer_model,[{role:'system',content:ANSWER_PROMPT},{role:'user',content:JSON.stringify({question,memories:memories.data})}]);
   appendFileSync(join(dir,'answers.jsonl'),JSON.stringify({step,question,memories:memories.data,answer})+'\n');
   report.steps.at(-1).detail_state=facts.find(f=>f.value==='black drip coffee')?.state;report.steps.at(-1).latte_state=facts.find(f=>f.value==='latte with whole milk')?.state;
  }
  console.log(JSON.stringify({completed:step+1,planned:steps.length}));
 }
}catch(e){report.error={code:e.code??null,message:e.message};}finally{await app.close();Models.prototype.json=original;}
report.finished_at=new Date().toISOString();report.fixed_extraction_injections=injections;report.completed_adds=report.steps.filter(s=>s.status===200).length;
if(report.completed_adds>1)report.answers_sha256=sha(readFileSync(join(dir,'answers.jsonl')));
writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');writeFileSync('reports/round2-transition-http-'+dir.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({run_dir:dir,completed_adds:report.completed_adds,error:report.error}));
