// Minimal deterministic reproduction of the coffee-detail loss observed in the
// actual E10_remember answer. This demonstrates the bug, not a passing repair.
import {readFileSync,writeFileSync,mkdtempSync,readdirSync} from 'node:fs';import {resolve,join} from 'node:path';
import {createHash} from 'node:crypto';import assert from 'node:assert/strict';
import {Extractor,hash} from '../../service/dist/extraction.js';import {TenantStore} from '../../service/dist/storage.js';import {configFromEnv} from '../../service/dist/config.js';
const dir=mkdtempSync(resolve('artifacts/round2-implicit-replacement-')),config=configFromEnv({MEMORY_MODE:'enhanced'}),store=new TenantStore(join(dir,'data'),'u');
const sha=x=>createHash('sha256').update(x).digest('hex');let proposal;
const extractor=new Extractor(config,{json:async()=>structuredClone(proposal),verify:async()=>[],embedBatch:async()=>{throw Error('Deterministic lexical fixture');}});
const f=(content,value)=>({content,subject:'user',predicate:'coffee_preference',value,scope:'',modality:'confirmed',cardinality:'single',sources:[{index:0,quote:content}],supersedes:[]});
const detail='I drink black drip coffee every morning, no milk, no sugar.',routine='I like the routine of just a plain cup of coffee.';
const before=[],after=[];
try{
 for(const [index,facts] of [[f(detail,'black drip coffee')],[f(detail,'black drip coffee'),f(routine,'plain cup of coffee routine')]].entries()){
  proposal={facts,operations:[]};const req={user_id:'u',session_id:'s',request_id:'r'+index,messages:[{role:'user',content:index?detail+' '+routine:detail,timestamp:`2026-01-0${index+1}T00:00:00Z`}]};
  const p=await extractor.prepare(req,store.snapshot('s'),AbortSignal.timeout(1000));assert.equal(p.operations.length,0);assert.ok(p.facts.every(f=>f.supersedes.length===0));
  store.commit(req,hash(JSON.stringify(req)),p,store.revision());(index?after:before).push(...store.facts());
 }
 const lost=after.find(f=>f.value==='black drip coffee'),kept=after.find(f=>f.value==='plain cup of coffee routine');assert.equal(lost.state,'superseded');assert.equal(kept.state,'active');
 assert.ok(lost.source_ids.some(id=>kept.source_ids.includes(id)),'Both details share the same new message');
 writeFileSync(join(dir,'states.json'),JSON.stringify({before,after})+'\n');writeFileSync(join(dir,'probe-source.mjs'),readFileSync(import.meta.filename));
 const report={protocol:'implicit-replacement-loss-reproduction-v1',run_dir:dir,source_sha256:Object.fromEntries(readdirSync('service/src').filter(p=>p.endsWith('.ts')).map(p=>[p,sha(readFileSync('service/src/'+p))])),probe_sha256:sha(readFileSync(import.meta.filename)),states_sha256:sha(readFileSync(join(dir,'states.json'))),reproduced:true,explicit_operations:0,explicit_replacements:0,before_detail_state:before[0].state,after_detail_state:lost.state,after_routine_state:kept.state,shared_source:true,scope:'Deterministic minimized fixture; model extraction and verifier are stubs, no live model call. Confirms current storage automatically hides a specific preference after a compatible routine statement. This is an unresolved bug, not a regression-test pass or an accuracy result.'};
 writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');writeFileSync('reports/round2-implicit-replacement-reproduction.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({run_dir:dir,reproduced:true}));
}finally{store.close();}
