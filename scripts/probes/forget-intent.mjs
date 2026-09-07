import {Extractor} from '../../service/dist/extraction.js';
import {configFromEnv} from '../../service/dist/config.js';
import {writeFileSync,readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const sha=p=>createHash('sha256').update(readFileSync(p)).digest('hex');
const config=configFromEnv({MEMORY_MODE:'enhanced'});
const fact={id:'known-quote',content:'The Acme quote is $123.',subject:'user',predicate:'service_quote',value:'$123',scope:'fan project',kind:'fact',modality:'confirmed',cardinality:'single',time_text:'',valid_from:null,valid_to:null,depends_on:[],supersedes:[],source_ids:['prior-source'],source_quotes:['The Acme quote is $123.'],created_at:'2026-01-01T00:00:00Z',observed_at:'2026-01-01T00:00:00Z',state:'active',vector:null,entities:['Acme'],revision:1};
const rows=[];
for(const content of ['Forget the Acme quote entirely.','Please forget the Acme quote entirely.','You can forget the Acme quote entirely.',"I do not need the Acme quote stored anymore.",'Do not forget the Acme quote.']){
  let calls=0;
  const models={json:async()=>{calls++;return {facts:[],operations:[{type:'forget',target_ids:['m0'],subject:'user',predicate:'service_quote',scope:'fan project',value:'$123',boundary:'value',source:{index:0,quote:content},reason:'Synthetic proposed deletion'}]};},embedBatch:async(texts)=>texts.map(()=>[1,...Array(767).fill(0)])};
  const extractor=new Extractor(config,models);
  const prepared=await extractor.prepare({request_id:'probe-'+rows.length,user_id:'synthetic-probe',session_id:'s',messages:[{role:'user',content,timestamp:'2026-01-02T00:00:00Z'}]},{revision:1,facts:[fact],tail:[],anchor:'2026-01-02'},AbortSignal.timeout(5000));
  rows.push({content,stub_model_calls:calls,returned_operations:prepared.operations.map(o=>({type:o.type,target_ids:o.target_ids})),degraded:prepared.degraded,returned_fact_count:prepared.facts.length});
}
const result={checked_at:new Date().toISOString(),scope:'Deterministic Extractor.prepare probe with a stubbed model, synthetic data, no network and no storage writes. Demonstrates a failure mechanism; does not reconstruct the unlogged model response of the historical benchmark.',source_sha256:{extraction:sha('service/src/extraction.ts'),compiled_extraction:sha('service/dist/extraction.js'),probe:sha('scripts/probes/forget-intent.mjs')},rows};
writeFileSync('reports/improvement-intent-probe.json',JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify(result));
