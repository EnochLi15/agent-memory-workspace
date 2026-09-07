// Independent semantic routing controls: real verifier calls, no extraction or
// commit. Expected routes are authored before inspecting model responses.
import {readFileSync,writeFileSync,mkdtempSync,mkdirSync,copyFileSync,readdirSync} from 'node:fs';import {resolve,join} from 'node:path';import {parseEnv} from 'node:util';import {createHash} from 'node:crypto';
import {configFromEnv} from '../../service/dist/config.js';import {Models} from '../../service/dist/models.js';import {sourceOperationWork} from '../../service/dist/source-operations.js';import {SOURCE_ROUTE_PROMPT,sourceRouteInput,decodeSourceRoute} from '../../service/dist/source-operation-routing.js';
const sha=x=>createHash('sha256').update(x).digest('hex'),dir=mkdtempSync(resolve('artifacts/round2-source-routing-controls-')),specPath='configs/round2-source-routing.json',spec=JSON.parse(readFileSync(specPath));
const config=configFromEnv({...parseEnv(readFileSync('.env','utf8')),...spec.defaults}),models=new Models(config),timestamp='2026-01-01T00:00:00Z';
process.env.MEMORY_MODEL_TRACE=join(dir,'private-model-trace.jsonl');process.env.MEMORY_MODEL_AUDIT=join(dir,'model-calls.jsonl');
const cases=[
 {name:'completed_appointment',messages:[['user',"My dentist appointment with Dr. Pham on Elm Street on April 9 is done. You don't need to keep track of that anymore."]],expected:['ordinary']},
 {name:'explicit_own_fact',messages:[['user','Forget my old access code 9182.']],expected:['ordinary']},
 {name:'unresolved_old_reference',messages:[['user',"Please don't store that."]],expected:['source_review']},
 {name:'explicit_assistant_falsehood',messages:[['user',"Your earlier claim that I go to Peak Gym was false. Please don't store that."]],expected:['source_review']},
 {name:'same_name_other_actor',messages:[['assistant','You go to Peak Gym.'],['user',"My brother goes to Peak Gym; I never said I do. Please don't store that claim about me."]],expected:['source_review']},
 {name:'mixed',messages:[['user','Forget my old access code 9182.'],['assistant','You go to Peak Gym.'],['user',"That claim about my gym is wrong. Please don't store that."]],expected:['ordinary','source_review']},
 {name:'quoted_claim_is_not_adoption',messages:[['user',"You said 'your dentist is Dr. Pham'. I was quoting you, not confirming it. Please don't store that."]],expected:['source_review']},
 {name:'unknown_personal_target_still_requires_binding',messages:[['user','Forget my backup account usernames.']],expected:['ordinary']}
];
writeFileSync(join(dir,'cases.json'),JSON.stringify(cases,null,2)+'\n');copyFileSync(import.meta.filename,join(dir,'probe-source.mjs'));copyFileSync(specPath,join(dir,'spec.json'));mkdirSync(join(dir,'source-snapshot'));const files=readdirSync('service/src').filter(f=>f.endsWith('.ts'));for(const f of files)copyFileSync('service/src/'+f,join(dir,'source-snapshot',f));
const report={protocol:'source-routing-controls-v1',run_dir:dir,scope:'Eight authored semantic routing controls with real verifier model. Routing only, no extraction/commit, not benchmark accuracy or independent human calibration.',started_at:new Date().toISOString(),source_sha256:Object.fromEntries(files.map(f=>[f,sha(readFileSync('service/src/'+f))])),cases_sha256:sha(readFileSync(join(dir,'cases.json'))),spec_sha256:sha(readFileSync(specPath)),results:[]};
console.log(JSON.stringify({run_dir:dir,status:'running'}));
for(const c of cases){const req={user_id:'route-control',request_id:c.name,session_id:'s',messages:c.messages.map(([role,content])=>({role,content,timestamp}))},history=[{id:'old',role:'assistant',content:'Your gym is Peak Gym.',session_id:'older',ordinal:0,timestamp,searchable:true}],work=sourceOperationWork(req,[],history),start=performance.now(),r={name:c.name,expected:c.expected};
 try{if(work.instructions.length!==c.expected.length)throw Error('Fixture did not produce expected instruction coverage');const raw=await models.json(SOURCE_ROUTE_PROMPT,JSON.stringify(sourceRouteInput(work)),AbortSignal.timeout(90000),{purpose:'source_operation_route',trace:{user_id:req.user_id,request_id:req.request_id}}),review=decodeSourceRoute(raw,work);r.actual=review.rows.map(x=>x.route);r.pass=JSON.stringify(r.actual)===JSON.stringify(c.expected);}
 catch(e){r.pass=false;r.error={code:e.code??null,message:e.message};}r.elapsed_ms=performance.now()-start;report.results.push(r);writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(r));}
report.finished_at=new Date().toISOString();report.passed=report.results.filter(r=>r.pass).length;report.total=cases.length;writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');writeFileSync('reports/'+dir.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');
