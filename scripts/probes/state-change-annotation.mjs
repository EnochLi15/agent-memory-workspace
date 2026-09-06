// Diagnostic only: identical saved evidence, optionally annotated from actual
// committed update events. No retrieval/storage mutation or Answer prompt change.
import {readFileSync,writeFileSync,appendFileSync,mkdtempSync} from 'node:fs';import {resolve,join} from 'node:path';import {parseEnv} from 'node:util';import {createHash} from 'node:crypto';import {createRequire} from 'node:module';import assert from 'node:assert/strict';
import {completion} from '../../eval/dist/models.js';import {ANSWER_PROMPT} from '../../eval/dist/runner.js';
const sha=x=>createHash('sha256').update(x).digest('hex'),parent=resolve(process.argv[2]??'artifacts/round2-transition-http-OYZhh0');
const reportIn=JSON.parse(readFileSync(join(parent,'report.json')));assert.equal(reportIn.completed_adds,3);
assert.equal(reportIn.answer_prompt_sha256,sha(ANSWER_PROMPT));
const saved=readFileSync(join(parent,'answers.jsonl'),'utf8').trim().split('\n').map(JSON.parse).find(r=>r.step===2);
const require=createRequire(new URL('../../service/package.json',import.meta.url)),Database=require('better-sqlite3'),db=new Database(join(parent,'data',sha('u'),'memory.sqlite'),{readonly:true,fileMustExist:true});let events,facts;
try{events=db.prepare('SELECT body FROM memory_events').all().map(r=>JSON.parse(r.body));facts=db.prepare('SELECT body FROM facts').all().map(r=>JSON.parse(r.body));}finally{db.close();}
const annotations=[];
const changed=saved.memories.map(m=>{
 const fact=facts.find(f=>f.id===m.id);if(!fact||fact.state!=='active'||fact.modality!=='confirmed')return m;
 const event=events.filter(e=>['update','correct','restore'].includes(e.type)&&e.after_ids.includes(fact.id)&&e.source_ids.some(id=>fact.source_ids.includes(id))).sort((a,b)=>b.revision-a.revision||b.ordinal-a.ordinal)[0];if(!event)return m;
 const note=`[recorded state change: ${event.type}; ${event.time_basis==='ordering'?'synthetic order marker, not an event date':'observed'}: ${event.observed_at}; this fact is the current target of that change]`;
 annotations.push({fact_id:fact.id,event_id:event.id,note});return {...m,content:m.content+'\n'+note};
});assert.equal(annotations.length,1);assert.deepEqual(changed.map(m=>m.id),saved.memories.map(m=>m.id));
const dir=mkdtempSync(resolve('artifacts/round2-state-annotation-')),env=parseEnv(readFileSync('.env','utf8'));
const inputs={baseline:{question:saved.question,memories:saved.memories},annotated:{question:saved.question,memories:changed}};
writeFileSync(join(dir,'inputs.json'),JSON.stringify(inputs)+'\n');writeFileSync(join(dir,'events.json'),JSON.stringify(events)+'\n');writeFileSync(join(dir,'probe-source.mjs'),readFileSync(import.meta.filename));
const report={protocol:'saved-state-change-annotation-v1',run_dir:dir,parent,parent_report_sha256:sha(readFileSync(join(parent,'report.json'))),saved_answers_sha256:sha(readFileSync(join(parent,'answers.jsonl'))),input_sha256:sha(JSON.stringify(inputs)),events_sha256:sha(JSON.stringify(events)),answer_prompt_sha256:sha(ANSWER_PROMPT),probe_sha256:sha(readFileSync(import.meta.filename)),model:reportIn.answer_model,annotations,planned:6,started_at:new Date().toISOString(),results:[],scope:'Three repeated Answer pairs over one fixed saved input. Same question, facts, order and fixed Answer prompt; annotation is derived from actual committed event metadata only. Not three full ingestion/retrieval runs, a held-out evaluation or a benchmark score.'};
for(const [repeat,order] of [['baseline','annotated'],['annotated','baseline'],['baseline','annotated']].entries())for(const variant of order){
 const start=performance.now();const row={repeat,variant,input_sha256:sha(JSON.stringify(inputs[variant]))};
 try{row.answer=await completion(env.MEMORY_LLM_BASE_URL,env.MEMORY_LLM_API_KEY,report.model,[{role:'system',content:ANSWER_PROMPT},{role:'user',content:JSON.stringify(inputs[variant])}]);row.status='answered';}
 catch(e){row.status='error';row.error=e.message;}
 row.elapsed_ms=performance.now()-start;appendFileSync(join(dir,'answers.jsonl'),JSON.stringify(row)+'\n');report.results.push({repeat,variant,status:row.status});console.log(JSON.stringify({completed:report.results.length,planned:6}));
}
report.finished_at=new Date().toISOString();report.answers_sha256=sha(readFileSync(join(dir,'answers.jsonl')));writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');writeFileSync('reports/round2-state-annotation-'+dir.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({complete:true,run_dir:dir}));
