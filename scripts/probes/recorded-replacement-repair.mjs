// Reuse an archived rejected repair input. Optional --live makes ONE new repair
// call; compatibility checks are not a semantic verdict, commit or benchmark.
import {readFileSync,writeFileSync,mkdtempSync,readdirSync} from 'node:fs';
import {resolve,join} from 'node:path';import {parseEnv} from 'node:util';
import {createHash} from 'node:crypto';import assert from 'node:assert/strict';
import {PATCH_PROMPT,applyRepair,replacementTargetGroups} from '../../service/dist/repair.js';
import {extractionSchema,replacementMatches,operationScopeProblem} from '../../service/dist/types.js';
import {Models} from '../../service/dist/models.js';import {configFromEnv} from '../../service/dist/config.js';
const sha=x=>createHash('sha256').update(x).digest('hex'),read=p=>JSON.parse(readFileSync(p,'utf8'));
const lines=p=>readFileSync(p,'utf8').trim().split('\n').map(JSON.parse);
const parent=resolve(process.argv[2]??'artifacts/round2-live-checkpoint-0Su0CB');
const last=lines(join(parent,'model-inputs.jsonl')).filter(r=>r.purpose==='repair').at(-1);assert.ok(last);
const archived=lines(join(parent,'model-proposals.jsonl')).find(r=>r.purpose==='repair'&&r.input_sha256===sha(last.input));assert.ok(archived);
const input=JSON.parse(last.input),proposal=extractionSchema.parse(input.FAILED_PROPOSAL);
const revisedInput={...input,REPLACEMENT_TARGET_GROUPS:replacementTargetGroups(proposal,input.EXISTING_FACTS)};
function inspect(raw){
 const p=applyRepair(proposal,raw,input.REPAIR_SCOPE),pool=[...input.EXISTING_FACTS,...p.facts.map((f,index)=>({...f,id:`new:${index}`}))];
 const replacements=p.facts.flatMap((f,index)=>f.supersedes.flatMap(id=>{const t=pool.find(t=>t.id===id);return !t||!replacementMatches(f,t)?[{fact:index,target:id,reason:t?'different_subject_property_or_scope':'unknown_target'}]:[];}));
 const operations=p.operations.flatMap((o,index)=>{const selected=pool.filter(f=>o.target_ids.includes(f.id)),code=o.target_ids.some(id=>!pool.some(f=>f.id===id))?'unknown_target':operationScopeProblem(o,selected);return code?[{operation:index,code}]:[];});
 const invalidSources=p.facts.flatMap((f,index)=>f.sources.filter(s=>!input.NEW_MESSAGES.find(m=>m.index===s.index)?.content.includes(s.quote)).map(s=>({fact:index,source:s.index})));
 return {compatible:!replacements.length&&!operations.length&&!invalidSources.length,replacement_violations:replacements,operation_violations:operations,invalid_fact_sources:invalidSources,fact_count:p.facts.length,operation_count:p.operations.length};
}
const dir=mkdtempSync(resolve('artifacts/round2-recorded-replacement-'));
writeFileSync(join(dir,'probe-source.mjs'),readFileSync(import.meta.filename));
writeFileSync(join(dir,'input.json'),JSON.stringify(revisedInput)+'\n');writeFileSync(join(dir,'system.txt'),PATCH_PROMPT);
const report={protocol:'archived-replacement-repair-v1',run_dir:dir,parent,
 parent_report_sha256:sha(readFileSync(join(parent,'report.json'))),archived_input_sha256:sha(last.input),archived_patch_sha256:sha(JSON.stringify(archived.output)),
 input_sha256:sha(JSON.stringify(revisedInput)),prompt_sha256:sha(PATCH_PROMPT),probe_sha256:sha(readFileSync(import.meta.filename)),
 source_sha256:Object.fromEntries(readdirSync('service/src').filter(p=>p.endsWith('.ts')).map(p=>[p,sha(readFileSync('service/src/'+p))])),
 archived_patch:inspect(archived.output),target_group_count:revisedInput.REPLACEMENT_TARGET_GROUPS.length,target_groups_chars:JSON.stringify(revisedInput.REPLACEMENT_TARGET_GROUPS).length,
 started_at:new Date().toISOString(),scope:'Exact archived failed proposal and feedback. New prompt and structural target groups; optional single fresh model patch. Deterministic compatibility and verbatim source checks only, no semantic acceptance, commit, whole-prefix latency or accuracy claim.'};
assert.equal(report.archived_patch.compatible,false,'Expected the saved incompatible replacement');
if(process.argv.includes('--live')){
 const spec=read('configs/round2-quality-erasure.json'),config=configFromEnv({...parseEnv(readFileSync('.env','utf8')),...spec.defaults,...spec.profiles.facts.environment});
 process.env.MEMORY_MODEL_AUDIT=join(dir,'model-usage.jsonl');const start=performance.now();
 try{
  const output=await new Models(config).json(PATCH_PROMPT,JSON.stringify(revisedInput),AbortSignal.timeout(90000),{purpose:'repair'});
  writeFileSync(join(dir,'output.json'),JSON.stringify(output)+'\n');report.output_sha256=sha(JSON.stringify(output));report.live_patch=inspect(output);report.status='returned';
 }catch(error){report.status='failed';report.error={code:error.code??null,message:error.message};}
 report.elapsed_ms=performance.now()-start;report.model=config.llmStageModels.repair??config.llmModel;
}else report.status='offline_replay';
report.finished_at=new Date().toISOString();writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');
writeFileSync('reports/round2-recorded-replacement-'+dir.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify({run_dir:dir,status:report.status,archived_patch:report.archived_patch,live_patch:report.live_patch,error:report.error}));
