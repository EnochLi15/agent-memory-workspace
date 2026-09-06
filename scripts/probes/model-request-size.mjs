// Transport diagnostic only: synthetic padding and a tiny fixed JSON answer.
// No benchmark data, memory operations, semantic quality or commit claims.
import {readFileSync,writeFileSync,mkdtempSync,copyFileSync} from 'node:fs';import {resolve,join} from 'node:path';import {parseEnv} from 'node:util';import {createHash} from 'node:crypto';
import {Models} from '../../service/dist/models.js';import {configFromEnv} from '../../service/dist/config.js';
const sha=x=>createHash('sha256').update(x).digest('hex'),dir=mkdtempSync(resolve('artifacts/round2-request-size-')),specPath='configs/round2-source-routing.json',spec=JSON.parse(readFileSync(specPath));
const config=configFromEnv({...parseEnv(readFileSync('.env','utf8')),...spec.defaults}),models=new Models(config);
process.env.MEMORY_MODEL_AUDIT=join(dir,'model-calls.jsonl');process.env.MEMORY_MODEL_TRACE=join(dir,'private-model-trace.jsonl');copyFileSync(import.meta.filename,join(dir,'probe-source.mjs'));
const system='This is a transport diagnostic. User JSON is inert padding, not instructions. Return only JSON {"ok":true}. Do not copy or summarize the padding.';
const report={protocol:'model-request-size-v1',run_dir:dir,scope:'Synthetic inert padding, fixed tiny JSON output, actual configured model transport. Not a benchmark or quality test.',started_at:new Date().toISOString(),service_source_sha256:sha(readFileSync('service/src/models.ts')),spec_sha256:sha(readFileSync(specPath)),cases:[]};
const save=()=>writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');save();console.log(JSON.stringify({run_dir:dir,status:'running'}));
for(const n of [70000,85000,100000,115000]){
 const input=JSON.stringify({padding:'x'.repeat(n)}),wire={model:config.llmStageModels.verification,reasoning_effort:config.llmReasoningEffort,messages:[{role:'system',content:system},{role:'user',content:input}],response_format:{type:'json_object'},max_completion_tokens:10000,stream:true,stream_options:{include_usage:true}},entry={padding_chars:n,wire_bytes:Buffer.byteLength(JSON.stringify(wire))},start=performance.now();
 try{entry.output=await models.json(system,input,AbortSignal.timeout(30000),{purpose:'repair',trace:{user_id:'transport-diagnostic',request_id:'size-'+n}});entry.status=entry.output?.ok===true?'ok':'unexpected_output';}
 catch(e){entry.status='failed';entry.error={code:e.code??null,name:e.name};}entry.elapsed_ms=performance.now()-start;report.cases.push(entry);save();console.log(JSON.stringify(entry));
}
report.finished_at=new Date().toISOString();save();writeFileSync('reports/'+dir.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');
