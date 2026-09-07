// Replay exact successful upstream model outputs, then call only the previously
// blocked source stage and subsequent stages. This is a component diagnostic.
import {readFileSync,writeFileSync,mkdtempSync,readdirSync} from 'node:fs';import {resolve,join} from 'node:path';import {createHash} from 'node:crypto';import {parseEnv} from 'node:util';import assert from 'node:assert/strict';
import {Extractor} from '../../service/dist/extraction.js';import {Models} from '../../service/dist/models.js';import {configFromEnv} from '../../service/dist/config.js';
const read=p=>JSON.parse(readFileSync(p)),lines=p=>readFileSync(p,'utf8').trim().split('\n').filter(Boolean).map(JSON.parse),sha=x=>createHash('sha256').update(x).digest('hex');
const snapshotPath=resolve(process.argv[2]),parent=resolve(process.argv[3]),saved=read(snapshotPath),previous=read(join(parent,'report.json'));
assert.equal(previous.status,'failed');assert.equal(previous.error.message,'Source erasure exceeds bounded candidate capacity');assert.equal(previous.captured_snapshot_sha256,sha(readFileSync(snapshotPath)));
const inputs=lines(join(parent,'model-inputs.jsonl')),outputs=lines(join(parent,'model-proposals.jsonl'));assert.equal(inputs.length,outputs.length);
const specPath=process.argv[4]??'configs/round2-quality-source-batches.json',spec=read(specPath),config=configFromEnv({...parseEnv(readFileSync('.env','utf8')),...spec.defaults});
const dir=mkdtempSync(resolve('artifacts/round2-source-batch-replay-'));process.env.MEMORY_MODEL_AUDIT=join(dir,'model-usage.jsonl');process.env.MEMORY_MODEL_TRACE=join(dir,'private-model-trace.jsonl');
const sourceReplayIndex=process.argv.indexOf('--source-decisions-run'),sourceReplay=sourceReplayIndex>=0?resolve(process.argv[sourceReplayIndex+1]):null;
const sourceDecisions=new Map(),usedSourceDecisions=new Set();let sourceReplayReport;
const evidenceKey=(input,c)=>JSON.stringify({source:input.SOURCES[c.source_slot],boundary:input.BOUNDARIES[c.boundary_slot],matching_words:c.matching_words});
if(sourceReplay){
 sourceReplayReport=read(join(sourceReplay,'report.json'));assert.equal(sourceReplayReport.captured_snapshot_sha256,sha(readFileSync(snapshotPath)));
 for(const row of lines(join(sourceReplay,'private-model-trace.jsonl')).filter(r=>r.purpose==='source_erasure'&&r.outcome==='ok')){
  const input=JSON.parse(row.input);assert.equal(row.output.decisions.length,input.CANDIDATES.length);
  for(const d of row.output.decisions){const c=input.CANDIDATES[d.index],key=evidenceKey(input,c);assert.ok(!sourceDecisions.has(key),'Ambiguous prior decision sampling');sourceDecisions.set(key,{decision:d,source:input.SOURCES[c.source_slot],trace_id:row.trace_id});}
 }
 assert.ok(sourceDecisions.size);
}
const model=new Models(config),original=model.json.bind(model);let replayed=0,live=0;
model.json=async(system,input,signal,context)=>{
 if(replayed<inputs.length){const i=replayed++;assert.equal(context.purpose,inputs[i].purpose);assert.equal(sha(system),inputs[i].prompt_sha256);assert.equal(input,inputs[i].input,'A replay is valid only for the exact model input');assert.equal(sha(input),outputs[i].input_sha256);return structuredClone(outputs[i].output);}
 if(sourceReplay&&context.purpose==='source_erasure'){
  const packed=JSON.parse(input);return {decisions:packed.CANDIDATES.map(c=>{const key=evidenceKey(packed,c),recorded=sourceDecisions.get(key);assert.ok(recorded,'Every reused decision needs exactly identical candidate evidence and authorization');usedSourceDecisions.add(key);return {...recorded.decision,index:c.index};})};
 }
 live++;return original(system,input,signal,context);
};
const budget=Math.floor(90000-previous.elapsed_ms-(sourceReplayReport?.elapsed_ms??0));assert.ok(budget>0,'No diagnostic budget remains');const report={protocol:'live-pre-failure-checkpoint-prepare-v1',run_dir:dir,sample:saved.provenance.sample,captured_snapshot_sha256:sha(readFileSync(snapshotPath)),parent_report_sha256:sha(readFileSync(join(parent,'report.json'))),source_sha256:Object.fromEntries(readdirSync('service/src').filter(p=>p.endsWith('.ts')).map(p=>[p,sha(readFileSync('service/src/'+p))])),spec_sha256:sha(readFileSync(specPath)),probe_sha256:sha(readFileSync(import.meta.filename)),snapshot_revision:saved.snapshot.revision,remaining_diagnostic_budget_ms:budget,started_at:new Date().toISOString(),scope:'Exact upstream input/output replay through erasure binding; fresh calls only after the original capacity failure. A fixed 90-second budget is reduced by parent elapsed time, but original extraction was already replayed in the parent. Not end-to-end latency, a fresh full sample or a benchmark score.'};
if(sourceReplay){report.source_decision_replay={run:sourceReplay,report_sha256:sha(readFileSync(join(sourceReplay,'report.json'))),reserved_elapsed_ms:sourceReplayReport.elapsed_ms};report.scope='Replay exact upstream model inputs/outputs and saved per-candidate source decisions only when the complete evidence/authorization payload matches. Omitted decisions must concern facts already certified erased by the preceding erasure stage; raw sources remain covered. Reuses model judgments, not a new full model-input replay or fresh benchmark. Both prior component elapsed times are reserved; original extraction was already replayed.';}
writeFileSync(join(dir,'probe-source.mjs'),readFileSync(import.meta.filename));const start=performance.now();
try{const p=await new Extractor(config,model).prepare(saved.request,saved.snapshot,AbortSignal.timeout(budget));
 if(sourceReplay){const erased=new Set(p.erasurePlan.decisions.filter(d=>d.effect==='erase').map(d=>d.fact_id));const omitted=[...sourceDecisions].filter(([key])=>!usedSourceDecisions.has(key)).map(([,r])=>({source_id:r.source.id,kind:r.source.kind,effect:r.decision.effect}));assert.ok(omitted.every(r=>r.kind==='fact'&&erased.has(r.source_id)),'Cannot omit any undecided fact or raw-source candidate');report.source_decision_replay.used=usedSourceDecisions.size;report.source_decision_replay.omitted=omitted;}
 writeFileSync(join(dir,'prepared.json'),JSON.stringify(p)+'\n');Object.assign(report,{status:'prepared',prepared_sha256:sha(readFileSync(join(dir,'prepared.json'))),fact_count:p.facts.length,operation_count:p.operations.length,source_decisions:p.sourceErasurePlan?.decisions.length});}
catch(e){report.status='failed';report.error={code:e.code??null,message:e.message};}
Object.assign(report,{elapsed_ms:performance.now()-start,recorded_generation_calls:replayed,live_generation_calls:live,finished_at:new Date().toISOString()});writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');writeFileSync('reports/round2-source-batch-replay-'+dir.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
