// Isolated recovery probes. Never write memory or replace a benchmark result.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFileSync,writeFileSync,mkdtempSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import {parseEnv} from 'node:util';

const root=resolve('.'),artifact=join(root,'artifacts/priority-quality-fix-20260908'),dist=join(artifact,'service-dist');
const sha=x=>createHash('sha256').update(x).digest('hex');
const rows=p=>readFileSync(p,'utf8').trim().split('\n').filter(Boolean).map(JSON.parse);
const snapshot=JSON.parse(readFileSync(join(artifact,'code-snapshot.json')));
for(const [name,digest] of Object.entries(snapshot.dist_hashes))assert.equal(sha(readFileSync(join(dist,name))),digest);
const {Models}=await import(pathToFileURL(join(dist,'models.js')));
const {decodeGroupedExtraction}=await import(pathToFileURL(join(dist,'extraction-groups.js')));
const {decodeSourceReferences,SOURCE_REFERENCE_PROTOCOL}=await import(pathToFileURL(join(dist,'source-references.js')));
const {humanQuote}=await import(pathToFileURL(join(dist,'verification.js')));
const original=rows('artifacts/priority-memops-four-20260908-01/candidate-model-trace.jsonl');
const syntax=original.find(r=>r.trace_id==='7209dba9-6fd2-4690-ac2a-f4d8139daba3');
const http=original.find(r=>r.trace_id==='50bd3149-2643-4973-8d66-4b07d08a7bdd');
assert.equal(syntax.outcome,'error');assert.equal(http.outcome,'error');assert.equal(http.output_text,'');
const config=JSON.parse(readFileSync('artifacts/priority-memops-four-20260908-01/candidate-service.json'));
const env=parseEnv(readFileSync('.env','utf8'));config.llmKey=env.MEMORY_LLM_API_KEY;
assert.equal(config.llmStageModels.extraction??config.llmModel,http.model);
config.modelTransportAttempts=1;
const dir=mkdtempSync(join(artifact,'recovery-probes-'));
process.env.MEMORY_MODEL_AUDIT=join(dir,'model-calls.jsonl');
process.env.MEMORY_MODEL_TRACE=join(dir,'private-model-trace.jsonl');
const report={protocol:'priority-quality-recovery-v1',started_at:new Date().toISOString(),candidate_manifest_sha256:sha(readFileSync(join(artifact,'code-snapshot.json'))),scope:'Isolated model/parser diagnostics; no add/search, no storage commit, no benchmark score replacement.'};
let calls=0;
const server=createServer(async(req,res)=>{
 for await(const _ of req){}calls++;
 res.writeHead(200,{'content-type':'text/event-stream'});
 for(let i=0;i<syntax.output_text.length;i+=97)res.write('data: '+JSON.stringify({choices:[{index:0,delta:{content:syntax.output_text.slice(i,i+97)},finish_reason:null}]})+'\n\n');
 res.end('data: '+JSON.stringify({choices:[{index:0,delta:{},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
try{
 assert.throws(()=>JSON.parse(syntax.output_text),SyntaxError);
 const value=await new Models({...config,llmBase:`http://127.0.0.1:${server.address().port}`,llmKey:'local-fixture'}).json(syntax.system,syntax.input,AbortSignal.timeout(3000),{purpose:'extraction'});
 const req=rows('eval/artifacts/priority-memops-four-20260908-01-candidate-memops/requests.jsonl').find(r=>r.path==='/add'&&r.body.request_id===syntax.identity.request_id)?.body;
 assert.ok(req,'Exact original HTTP request is required');
 const protocol=JSON.parse(syntax.input).EXTRACTION_PROTOCOL;
 const decoded=protocol===SOURCE_REFERENCE_PROTOCOL?decodeSourceReferences(value,req):decodeGroupedExtraction(value,req);
 assert.ok(decoded.facts.every(f=>f.sources.every(s=>humanQuote(req,s.index,s.quote))));
 assert.ok(decoded.operations.every(o=>humanQuote(req,o.source.index,o.source.quote)));
 report.json_recovery={original_trace_id:syntax.trace_id,output_sha256:sha(syntax.output_text),status:'parsed_and_group_schema_and_source_quotes_valid',groups:value.message_groups.length,facts:decoded.facts.length,operations:decoded.operations.length,local_http_calls:calls,remote_model_calls:0,normalization:rows(process.env.MEMORY_MODEL_AUDIT)[0],semantic_verification:'Not a new semantic verdict; normal independent verification remains required before storage.'};
}finally{await new Promise(r=>server.close(()=>r()));}
writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify({stage:'recorded_json_recovery',status:report.json_recovery.status,facts:report.json_recovery.facts}));
const start=performance.now(),before=rows(process.env.MEMORY_MODEL_AUDIT).length;
try{
 const output=await new Models(config).json(http.system,http.input,AbortSignal.timeout(config.addTimeout),{purpose:'extraction'});
 report.http_replay={status:'returned',output_sha256:sha(JSON.stringify(output))};
}catch{report.http_replay={status:'failed'};}
Object.assign(report.http_replay,{original_trace_id:http.trace_id,input_sha256:sha(http.input),system_sha256:sha(http.system),model:http.model,max_transport_attempts:1,elapsed_ms:performance.now()-start,audit:rows(process.env.MEMORY_MODEL_AUDIT).slice(before),limitation:'Current replay cannot retroactively establish the original HTTP 400 cause. No retry policy or input content was changed.'});
report.finished_at=new Date().toISOString();
writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');
writeFileSync('reports/priority-quality-recovery-20260908.json',JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report.http_replay));
