// Fixed critic controls, separate from end-to-end or benchmark accuracy.
import {readFileSync,writeFileSync,mkdtempSync,appendFileSync} from 'node:fs';
import {join,resolve} from 'node:path';import {parseEnv} from 'node:util';import {createHash} from 'node:crypto';
import {configFromEnv} from '../../service/dist/config.js';import {Models} from '../../service/dist/models.js';import {extractionSchema} from '../../service/dist/types.js';
const e=parseEnv(readFileSync('.env','utf8'));const dir=mkdtempSync(join(resolve('artifacts'),'round2-fact-verification-controls-'));
process.env.MEMORY_MODEL_AUDIT=join(dir,'model-usage.jsonl');
const models=new Models({...configFromEnv(e),llmModel:'gpt-5.5',llmReasoningEffort:'low'});
const cases=[
 {id:'valid_plan',text:'I might move to Paris next year.',content:'User might move to Paris next year.',predicate:'move_plan',value:'Paris',modality:'tentative',accept:true},
 {id:'plan_as_current_state',text:'I might move to Paris next year.',content:'User currently lives in Paris.',predicate:'current_city',value:'Paris',modality:'confirmed',accept:false},
 {id:'wrong_actor',text:'My friend Kevin lives in Paris.',content:'User lives in Paris.',predicate:'current_city',value:'Paris',modality:'confirmed',accept:false},
 {id:'negated_preference',text:'I do not like skiing.',content:'User likes skiing.',predicate:'hobby',value:'skiing',modality:'confirmed',accept:false},
 {id:'question_as_personal_fact',text:'Is Paris a good city to live in?',content:'User lives in Paris.',predicate:'current_city',value:'Paris',modality:'confirmed',accept:false},
 {id:'valid_current_state',text:'My browser is Firefox.',content:'User uses Firefox as their browser.',predicate:'default_browser',value:'Firefox',modality:'confirmed',accept:true},
];
const results=[];for(const c of cases){
 const req={request_id:c.id,user_id:'u',session_id:'s',messages:[{role:'user',content:c.text,timestamp:'2026-01-01T00:00:00Z'}]};
 const proposal=extractionSchema.parse({facts:[{content:c.content,subject:'user',predicate:c.predicate,value:c.value,modality:c.modality,sources:[{index:0,quote:c.text}]}],operations:[]});
 const start=performance.now();let result;
 try{const issues=await models.verify(proposal,req,[],[],AbortSignal.timeout(55000));result={id:c.id,expected_accept:c.accept,issues,accepted:issues.length===0,pass:(issues.length===0)===c.accept,elapsed_ms:performance.now()-start};}
 catch(error){result={id:c.id,expected_accept:c.accept,error:error.message,pass:false,elapsed_ms:performance.now()-start};}
 results.push(result);appendFileSync(join(dir,'results.jsonl'),JSON.stringify(result)+'\n');console.log(JSON.stringify(result));
}
const sha=x=>createHash('sha256').update(x).digest('hex');
const report={protocol:'fact-verification-model-controls-v1',model:'gpt-5.5',reasoning_effort:'low',cases_sha256:sha(JSON.stringify(cases)),source_sha256:Object.fromEntries(['service/src/verification.ts','service/src/models.ts'].map(p=>[p,sha(readFileSync(p))])),run_dir:dir,results,passed:results.filter(r=>r.pass).length,planned:cases.length,scope:'Six fixed assistant-authored critic controls with real API calls; not new benchmark answers or full service inference.'};
writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');writeFileSync('reports/round2-fact-verification-model-controls.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({complete:true,passed:report.passed,planned:report.planned}));
