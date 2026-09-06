// Fixed explicit-intention controls, including the archived D24 proposal.
import {readFileSync,writeFileSync,appendFileSync,mkdtempSync,readdirSync} from 'node:fs';import {resolve,join} from 'node:path';import {pathToFileURL} from 'node:url';import {parseEnv} from 'node:util';import {createHash} from 'node:crypto';import assert from 'node:assert/strict';
const runtime=resolve(process.argv[2]??'service'),sha=x=>createHash('sha256').update(x).digest('hex'),read=p=>JSON.parse(readFileSync(p,'utf8')),lines=p=>readFileSync(p,'utf8').trim().split('\n').map(JSON.parse);
const {Models}=await import(pathToFileURL(join(runtime,'dist/models.js'))),{configFromEnv}=await import(pathToFileURL(join(runtime,'dist/config.js'))),{extractionSchema}=await import(pathToFileURL(join(runtime,'dist/types.js')));
const parent=resolve('artifacts/round2-failure-prefixes/2026-09-06T04-42-08-565Z-k61M9D');
const record=lines(join(parent,'extraction-proposals.jsonl')).filter(r=>r.output.fact_checks&&r.input.includes("I'll definitely do my research")).at(-1),input=JSON.parse(record.input);
assert.equal(input.TARGET_FACTS.length,0);
const archived=extractionSchema.parse(input.PROPOSAL),req={request_id:'archived-D24-plan',user_id:'plan-controls',session_id:'s',messages:input.NEW_MESSAGES.map(({index,...message})=>message)};
assert.equal(archived.facts[0].modality,'confirmed');assert.equal(archived.facts[1].modality,'tentative');
const fixed=structuredClone(archived);fixed.facts[0].modality='tentative';
const cases=[{id:'archived_firm_plan_confirmed',req,proposal:archived,accept:false,rejection_prefix:'fact 0:'},{id:'archived_firm_plan_tentative',req,proposal:fixed,accept:true}];
for(const [id,text,predicate,modality,accept] of [
 ['firm_plan_confirmed','I will definitely consult a financial advisor before making a decision.','consultation_plan','confirmed',false],
 ['firm_plan_tentative','I will definitely consult a financial advisor before making a decision.','consultation_plan','tentative',true],
 ['completed_consultation','I consulted a financial advisor yesterday.','consultation','confirmed',true],
 ['current_preference','I prefer Firefox as my browser.','browser_preference','confirmed',true],
])cases.push({id,req:{request_id:id,user_id:'plan-controls',session_id:'s',messages:[{role:'user',content:text,timestamp:'2026-01-01T00:00:00Z'}]},proposal:extractionSchema.parse({facts:[{content:text,subject:'user',predicate,value:text,modality,sources:[{index:0,quote:text}]}]}),accept,...(!accept?{rejection_prefix:'fact 0:'}:{})});
const dir=mkdtempSync(resolve('artifacts/round2-firm-plan-controls-')),env=parseEnv(readFileSync('.env','utf8'));
process.env.MEMORY_MODEL_AUDIT=join(dir,'model-usage.jsonl');
writeFileSync(join(dir,'cases.json'),JSON.stringify(cases)+'\n');writeFileSync(join(dir,'probe-source.mjs'),readFileSync(import.meta.filename));
const report={protocol:'firm-plan-modality-controls-v1',runtime,run_dir:dir,model:'gpt-5.5',reasoning_effort:'low',cases_sha256:sha(readFileSync(join(dir,'cases.json'))),source_sha256:Object.fromEntries(readdirSync(join(runtime,'src')).filter(p=>p.endsWith('.ts')).map(p=>[p,sha(readFileSync(join(runtime,'src',p)))])),probe_sha256:sha(readFileSync(import.meta.filename)),planned:cases.length,started_at:new Date().toISOString(),results:[],scope:'Six exposed development controls. An unfulfilled firm intention remains tentative under the memory ontology; completed actions and current preferences remain confirmed. Each case uses a fresh checker session. Not benchmark accuracy or a fresh full prefix.'};
writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');
for(const c of cases){
 const model=new Models(configFromEnv({...env,MEMORY_LLM_MODEL:'gpt-5.5',MEMORY_VERIFICATION_MODEL:'gpt-5.5',MEMORY_LLM_REASONING_EFFORT:'low',MEMORY_VERIFICATION_FORMAT:'compact'})),json=model.json.bind(model);let calls=0;const start=performance.now();
 model.json=async(system,input,signal,context)=>{calls++;const raw=await json(system,input,signal,context);appendFileSync(join(dir,'model-proposals.jsonl'),JSON.stringify({id:c.id,prompt_sha256:sha(system),input,output:raw})+'\n');return raw;};
 let result;
 try{const issues=await model.verify(c.proposal,c.req,[],[],AbortSignal.timeout(90000));const accepted=!issues.length;result={id:c.id,status:'judged',accepted,expected_accept:c.accept,issues,pass:accepted===c.accept&&(!c.rejection_prefix||issues.some(i=>i.startsWith(c.rejection_prefix)))};}
 catch(error){result={id:c.id,status:'error',pass:false,error:error.message};}
 result.elapsed_ms=performance.now()-start;result.generation_calls=calls;report.results.push(result);writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(result));
}
report.finished_at=new Date().toISOString();report.passed=report.results.filter(x=>x.pass).length;writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');writeFileSync('reports/round2-firm-plan-controls-'+dir.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({complete:true,passed:report.passed,planned:report.planned,run_dir:dir}));
