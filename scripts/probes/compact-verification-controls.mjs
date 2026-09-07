// Counterbalanced encoding controls. These are checker calls, not fresh ingestion.
import {readFileSync,writeFileSync,mkdtempSync,appendFileSync,readdirSync,mkdirSync,copyFileSync} from 'node:fs';
import {join,resolve} from 'node:path';import {parseEnv} from 'node:util';import {createHash} from 'node:crypto';import assert from 'node:assert/strict';
import {configFromEnv} from '../../service/dist/config.js';import {Models} from '../../service/dist/models.js';import {extractionSchema} from '../../service/dist/types.js';
const sha=x=>createHash('sha256').update(x).digest('hex'),read=p=>JSON.parse(readFileSync(p,'utf8')),lines=p=>readFileSync(p,'utf8').trim().split('\n').map(x=>JSON.parse(x));
const dir=mkdtempSync(resolve('artifacts/round2-compact-controls-')),env=parseEnv(readFileSync('.env','utf8'));
const request=(id,text)=>({request_id:id,user_id:'controls',session_id:'s',messages:[{role:'user',content:text,timestamp:'2026-01-01T00:00:00Z'}]});
const fact=(text,content,predicate,value,modality='confirmed')=>({content,subject:'user',predicate,value,modality,sources:[{index:0,quote:text}]});
const stored=(predicate,value)=>({...extractionSchema.parse({facts:[fact(value,value,predicate,value)]}).facts[0],id:'old',depends_on:[],supersedes:[],source_ids:['old-source'],source_quotes:[value],created_at:'2025-12-01T00:00:00Z',observed_at:'2025-12-01T00:00:00Z',state:'active',vector:null,entities:[],revision:1});
const cases=[];
for(const [id,text,content,predicate,value,modality,accept] of [
 ['valid_plan','I might move to Paris next year.','User might move to Paris next year.','move_plan','Paris','tentative',true],
 ['plan_as_current_state','I might move to Paris next year.','User currently lives in Paris.','current_city','Paris','confirmed',false],
 ['wrong_actor','My friend Kevin lives in Paris.','User lives in Paris.','current_city','Paris','confirmed',false],
 ['negated_preference','I do not like skiing.','User likes skiing.','hobby','skiing','confirmed',false],
 ['question_as_personal_fact','Is Paris a good city to live in?','User lives in Paris.','current_city','Paris','confirmed',false],
 ['valid_current_state','My browser is Firefox.','User uses Firefox as their browser.','default_browser','Firefox','confirmed',true],
])cases.push({id,req:request(id,text),proposal:extractionSchema.parse({facts:[fact(text,content,predicate,value,modality)]}),facts:[],accept});
for(const [id,text,accept] of [['missing_plan','I will visit my sister next Saturday.',false],['generic_question','How do rainbows form?',true]])cases.push({id,req:request(id,text),proposal:extractionSchema.parse({facts:[]}),facts:[],accept});
for(const [id,text,predicate,accept] of [['authorized_forget','Forget my default browser.','default_browser',true],['negated_forget','Do not forget my default browser.','default_browser',false],['wrong_forget_target','Forget my default browser.','current_city',false]]){
 cases.push({id,req:request(id,text),proposal:extractionSchema.parse({facts:[],operations:[{type:'forget',subject:'user',predicate:'default_browser',target_ids:['old'],source:{index:0,quote:text}}]}),facts:[stored(predicate,predicate==='current_city'?'Boston':'Firefox')],accept});
}
for(const [id,predicate,accept] of [['valid_replacement','current_city',true],['wrong_replacement','default_browser',false]]){
 const text='I moved from Boston to Paris.';cases.push({id,req:request(id,text),proposal:extractionSchema.parse({facts:[{...fact(text,'User moved to Paris.','current_city','Paris'),supersedes:['old']}]}),facts:[stored(predicate,predicate==='current_city'?'Boston':'Firefox')],accept});
}
const simpleCount=cases.length;
// Three repetitions of the same archived rejected proposal, unchanged by either
// format. Reconstruct only ephemeral same-chunk IDs and verify their old context.
const parent=resolve('artifacts/round2-failure-prefixes/2026-09-06T03-14-50-774Z-tZoQwl'),prior=read(join(parent,'results.json')),row=prior.cases[0];
const input=JSON.parse(lines(join(parent,'model-inputs.jsonl')).filter(x=>x.purpose==='verification').at(-2).input);
const original=lines('eval/artifacts/holdout-v2-U3-memops/requests.jsonl').find(x=>x.body?.request_id===row.adds.at(-1).original_request_id).body;
const req={...original,user_id:row.user_id,request_id:row.user_id+':'+(row.adds.length-1)};
assert.deepEqual(req.messages,input.NEW_MESSAGES.map(({index,...m})=>m));const proposal=extractionSchema.parse(input.PROPOSAL);
const facts=[...read(join(parent,'C30_update-state.json')).facts,...proposal.facts.map((f,i)=>({...f,id:sha(`${req.user_id}\0${req.request_id}\0fact\0${i}`),source_ids:[],source_quotes:f.sources.map(s=>s.quote),created_at:'',observed_at:'',state:'active',vector:null,entities:[],revision:0}))];
for(const t of input.TARGET_FACTS){const actual=facts.find(f=>f.id===t.id);assert.ok(actual);for(const [k,v] of Object.entries(t))assert.deepEqual(actual[k],v);}
for(let i=0;i<3;i++)cases.push({id:'archived_C30_'+i,req,proposal,facts,accept:false,kind:'fixed_long_proposal'});
process.env.MEMORY_MODEL_AUDIT=join(dir,'model-usage.jsonl');
const sourceDir=join(dir,'source-snapshot');mkdirSync(sourceDir);const sources=readdirSync('service/src').filter(p=>p.endsWith('.ts'));
for(const p of sources)copyFileSync('service/src/'+p,join(sourceDir,p));copyFileSync(import.meta.filename,join(dir,'probe-source.mjs'));
writeFileSync(join(dir,'cases.json'),JSON.stringify(cases)+'\n');
const report={protocol:'counterbalanced-verification-encoding-controls-v1',run_dir:dir,model:'gpt-5.5',reasoning_effort:'low',source_sha256:Object.fromEntries(sources.map(p=>['service/src/'+p,sha(readFileSync('service/src/'+p))])),probe_sha256:sha(readFileSync(import.meta.filename)),cases_sha256:sha(readFileSync(join(dir,'cases.json'))),simple_controls:simpleCount,fixed_long_repeats:3,planned:cases.length*2,started_at:new Date().toISOString(),results:[],scope:'13 fixed assistant-authored controls and three checker-only repetitions of one exposed archived C30 proposal. Same model, full evidence and 90-second checker deadline; format order alternates by case. No ingestion, new benchmark score, or three full-pipeline repeats.'};
writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');
for(const [index,c] of cases.entries())for(const format of (index%2?['compact','verbose']:['verbose','compact'])){
 const model=new Models(configFromEnv({...env,MEMORY_LLM_MODEL:'gpt-5.5',MEMORY_VERIFICATION_MODEL:'gpt-5.5',MEMORY_LLM_REASONING_EFFORT:'low',MEMORY_VERIFICATION_FORMAT:format}));
 const json=model.json.bind(model);let calls=0;model.json=async(system,input,signal,context)=>{calls++;const output=await json(system,input,signal,context);appendFileSync(join(dir,'model-proposals.jsonl'),JSON.stringify({case:c.id,format,prompt_sha256:sha(system),input_sha256:sha(input),input,output})+'\n');return output;};
 const start=performance.now();let result;
 try{const issues=await model.verify(c.proposal,c.req,c.facts,[],AbortSignal.timeout(90000));result={id:c.id,format,kind:c.kind??'control',status:'judged',expected_accept:c.accept,accepted:issues.length===0,pass:(issues.length===0)===c.accept,issues};}
 catch(error){result={id:c.id,format,kind:c.kind??'control',status:'error',expected_accept:c.accept,pass:false,error:error.message};}
 result.elapsed_ms=performance.now()-start;result.generation_calls=calls;report.results.push(result);appendFileSync(join(dir,'results.jsonl'),JSON.stringify(result)+'\n');writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({completed:report.results.length,planned:report.planned,id:c.id,format,pass:result.pass,status:result.status}));
}
report.finished_at=new Date().toISOString();report.passed=report.results.filter(r=>r.pass).length;
for(const p of ['model-usage.jsonl','model-proposals.jsonl','results.jsonl'])report[p+'_sha256']=sha(readFileSync(join(dir,p)));
writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');writeFileSync('reports/round2-compact-controls-'+dir.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({complete:true,passed:report.passed,planned:report.planned,run_dir:dir}));
