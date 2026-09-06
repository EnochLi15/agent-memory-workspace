// Fresh model preparation on an immutable pre-failure snapshot. Not a new full prefix.
import {readFileSync,writeFileSync,appendFileSync,mkdtempSync,readdirSync,mkdirSync,copyFileSync} from 'node:fs';import {resolve,join,dirname} from 'node:path';import {parseEnv} from 'node:util';import {createHash} from 'node:crypto';import assert from 'node:assert/strict';
import {Extractor} from '../../service/dist/extraction.js';import {Models} from '../../service/dist/models.js';import {configFromEnv} from '../../service/dist/config.js';
const sha=x=>createHash('sha256').update(x).digest('hex'),read=p=>JSON.parse(readFileSync(p,'utf8')),lines=p=>readFileSync(p,'utf8').trim().split('\n').map(JSON.parse);
let req,snapshot,sample,provenance;
if(process.argv[2]==='--snapshot'){
 const path=resolve(process.argv[3]),bytes=readFileSync(path),saved=JSON.parse(bytes),capture=read(join(dirname(path),'report.json'));
 assert.equal(saved.protocol,'failed-development-chunk-snapshot-v1');assert.equal(capture.cases.find(c=>c.file===path)?.sha256,sha(bytes));
 req=saved.request;snapshot=saved.snapshot;sample=saved.provenance.sample;provenance={captured_snapshot_sha256:sha(bytes),capture_report_sha256:sha(readFileSync(join(dirname(path),'report.json'))),parent_provenance:saved.provenance};
}else{
 const parent=resolve(process.argv[2]);sample=process.argv[3];
 const results=read(join(parent,'results.json')),row=results.cases.find(x=>x.user_id.endsWith(':'+sample));assert.equal(row.status,'failed','Checkpoint must precede a rejected add');
 const last=lines(join(parent,'model-inputs.jsonl')).filter(x=>x.purpose==='extraction').at(-1),input=JSON.parse(last.input),state=read(join(parent,sample+'-state.json'));
 assert.equal(state.revision,row.adds.filter(a=>a.status===200).length);
 const original=lines('eval/artifacts/holdout-v2-U3-memops/requests.jsonl').find(x=>x.body?.request_id===row.adds.at(-1).original_request_id).body;
 req={...original,user_id:row.user_id,request_id:row.user_id+':'+(row.adds.length-1)};assert.deepEqual(req.messages,input.NEW_MESSAGES.map(({index,...m})=>m));
 snapshot={facts:state.facts,tail:input.CONTEXT_ONLY,anchor:input.OBSERVATION_DATE,revision:state.revision};
 provenance={parent_results_sha256:sha(readFileSync(join(parent,'results.json'))),parent_state_sha256:sha(readFileSync(join(parent,sample+'-state.json'))),parent_model_input_sha256:sha(last.input)};
}
const specIndex=process.argv.indexOf('--spec'),specPath=specIndex>=0?process.argv[specIndex+1]:'configs/round2-quality.json';if(!specPath)throw Error('Missing --spec path');
const dir=mkdtempSync(resolve('artifacts/round2-live-checkpoint-')),env=parseEnv(readFileSync('.env','utf8')),spec=read(specPath);
const config={...configFromEnv({...env,...spec.defaults,...spec.profiles.facts.environment}),dataDir:join(dir,'unused')};process.env.MEMORY_MODEL_AUDIT=join(dir,'model-usage.jsonl');
mkdirSync(join(dir,'source-snapshot'));const sources=readdirSync('service/src').filter(p=>p.endsWith('.ts'));for(const p of sources)copyFileSync('service/src/'+p,join(dir,'source-snapshot',p));copyFileSync(import.meta.filename,join(dir,'probe-source.mjs'));
const model=new Models(config),json=model.json.bind(model),verify=model.verify.bind(model);let calls=0;
model.json=async(system,input,signal,context)=>{calls++;appendFileSync(join(dir,'model-inputs.jsonl'),JSON.stringify({purpose:context?.purpose,prompt_sha256:sha(system),input})+'\n');const output=await json(system,input,signal,context);appendFileSync(join(dir,'model-proposals.jsonl'),JSON.stringify({purpose:context?.purpose,input_sha256:sha(input),output})+'\n');return output;};
model.verify=async(...args)=>{const findings=await verify(...args);appendFileSync(join(dir,'verification-findings.jsonl'),JSON.stringify({findings})+'\n');return findings;};
const report={protocol:'live-pre-failure-checkpoint-prepare-v1',run_dir:dir,sample,...provenance,source_sha256:Object.fromEntries(sources.map(p=>[p,sha(readFileSync('service/src/'+p))])),probe_sha256:sha(readFileSync(import.meta.filename)),spec_sha256:sha(readFileSync(specPath)),verification_response_format:config.verificationResponseFormat??'json_object',snapshot_revision:snapshot.revision,stage_models:config.llmStageModels,model_budget_ms:Math.min(95000,Math.max(500,config.addTimeout-25000)),started_at:new Date().toISOString(),status:'running',scope:'Fresh extraction, repair, verification and local embedding for the single failed chunk on the exact saved preceding snapshot. No HTTP add or commit; not a fresh full-prefix run or benchmark score.'};writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');
const start=performance.now();try{
 const prepared=await new Extractor(config,model).prepare(req,snapshot,AbortSignal.timeout(config.addTimeout));writeFileSync(join(dir,'prepared.json'),JSON.stringify(prepared)+'\n');Object.assign(report,{status:'prepared',fact_count:prepared.facts.length,operation_count:prepared.operations.length,degraded:prepared.degraded,prepared_sha256:sha(readFileSync(join(dir,'prepared.json')))});
}catch(error){Object.assign(report,{status:'failed',error:{code:error.code??null,message:error.message}});}
Object.assign(report,{generation_calls:calls,elapsed_ms:performance.now()-start,finished_at:new Date().toISOString()});writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');writeFileSync('reports/round2-live-checkpoint-'+dir.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
