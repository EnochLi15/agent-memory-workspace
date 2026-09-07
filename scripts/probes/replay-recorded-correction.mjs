// Replay an already exposed, recorded extraction through current preparation and
// storage. No extraction/verification model is called: this isolates commit logic.
import {readFileSync,writeFileSync,mkdtempSync,readdirSync} from 'node:fs';
import {join,resolve} from 'node:path';
import {createHash} from 'node:crypto';
import {Extractor,hash} from '../../service/dist/extraction.js';
import {TenantStore} from '../../service/dist/storage.js';
import {configFromEnv} from '../../service/dist/config.js';
import {retrieve} from '../../service/dist/retrieval.js';
const parent=resolve(process.argv[2]);
const recordsPath=join(parent,'extraction-proposals.jsonl');
const records=readFileSync(recordsPath,'utf8').trim().split('\n').map(JSON.parse);
const extracted=records.find(r=>r.output?.facts?.some(f=>f.predicate==='recurring_investment_amount'&&f.value==='$250 every two weeks'));
const validated=records.map(r=>JSON.parse(r.input)).find(i=>i.PROPOSAL?.operations?.some(o=>o.predicate==='recurring_investment_amount'));
if(!extracted||!validated)throw Error('Expected recorded C30 correction absent');
const dir=mkdtempSync(join(resolve('artifacts'),'round2-recorded-correction-'));
const config=configFromEnv({MEMORY_MODE:'enhanced'}),store=new TenantStore(join(dir,'data'),'u');
const req={request_id:'recorded-correction',user_id:'u',session_id:'s',messages:validated.NEW_MESSAGES.map(({index,...m})=>m)};
const x=new Extractor(config,{json:async()=>structuredClone(extracted.output),verify:async()=>[],embedBatch:async()=>{throw Error('Recorded transaction replay uses lexical evidence only');}});
const sha=x=>createHash('sha256').update(x).digest('hex');
try{
 const p=await x.prepare(req,store.snapshot('s'),AbortSignal.timeout(10000));
 store.commit(req,hash(JSON.stringify(req)),p,0);
 const facts=store.facts(),events=store.events();
 const rows=retrieve(store,{user_id:'u',query:'current automatic investment amount schedule fund',top_k:100},null,config).data;
 const old=facts.filter(f=>f.predicate==='recurring_investment_amount'&&f.value==='$200 every two weeks');
 const current=facts.filter(f=>f.predicate==='recurring_investment_amount'&&f.value==='$250 every two weeks');
 const event=events.find(e=>e.type==='correct');
 const checks={old_amount_retracted:old.length===1&&old.every(f=>f.state==='retracted'),new_amount_active:current.length===1&&current.every(f=>f.state==='active'),event_targets_old_only:!!event&&event.before_ids.length===1&&event.before_ids[0]===old[0]?.id&&event.after_ids.includes(current[0]?.id),new_amount_retrievable:rows.some(r=>r.id===current[0]?.id)};
 const state=join(dir,'state.json');writeFileSync(state,JSON.stringify({facts,events,memories:rows},null,2)+'\n');
 const source_sha256=Object.fromEntries(readdirSync('service/src').filter(f=>f.endsWith('.ts')).sort().map(f=>['service/src/'+f,sha(readFileSync('service/src/'+f))]));
 const report={protocol:'recorded-correction-transaction-replay-v1',parent,recorded_proposals_sha256:sha(readFileSync(recordsPath)),source_sha256,probe_sha256:sha(readFileSync('scripts/probes/replay-recorded-correction.mjs')),run_dir:dir,state_sha256:sha(readFileSync(state)),checks,pass:Object.values(checks).every(Boolean),degraded:p.degraded,scope:'The original recorded C30 extraction and participant messages are replayed without new LLM inference or semantic revalidation. This isolates the preparation/commit correction bug; it is not a new full-prefix or benchmark pass.'};
 writeFileSync('reports/round2-C30-recorded-correction-replay.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
}finally{store.close();}
