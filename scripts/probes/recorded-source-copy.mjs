// Full saved E03 context, stopping at semantic verification. No API or commit.
import {readFileSync} from 'node:fs';import {resolve,join} from 'node:path';import {pathToFileURL} from 'node:url';import {createHash} from 'node:crypto';import assert from 'node:assert/strict';
const runtime=resolve(process.argv[2]??'service'),parent=resolve('artifacts/round2-failure-prefixes/2026-09-06T04-40-36-878Z-IdlENR');
const read=p=>JSON.parse(readFileSync(p,'utf8')),lines=p=>readFileSync(p,'utf8').trim().split('\n').map(JSON.parse),sha=x=>createHash('sha256').update(x).digest('hex');
const {Extractor}=await import(pathToFileURL(join(runtime,'dist/extraction.js'))),{configFromEnv}=await import(pathToFileURL(join(runtime,'dist/config.js'))),{ServiceError}=await import(pathToFileURL(join(runtime,'dist/types.js')));
const result=read(join(parent,'results.json')),row=result.cases[0],recordedInput=lines(join(parent,'model-inputs.jsonl')).filter(x=>x.purpose==='extraction').at(-1);
const input=JSON.parse(recordedInput.input),proposalRecord=lines(join(parent,'extraction-proposals.jsonl')).find(x=>x.input_sha256===recordedInput.input_sha256);
assert.ok(proposalRecord);
const original=lines('eval/artifacts/holdout-v2-U3-memops/requests.jsonl').find(x=>x.body?.request_id===row.adds.at(-1).original_request_id).body;
const req={...original,user_id:row.user_id,request_id:row.user_id+':'+(row.adds.length-1)},state=read(join(parent,'E03_reflect-state.json'));
const snapshot={facts:state.facts,tail:input.CONTEXT_ONLY,anchor:input.OBSERVATION_DATE,revision:state.revision};
let generations=0,verifications=0,source=null;
const model={json:async(_system,user)=>{generations++;if(generations===1){assert.deepEqual(JSON.parse(user),input);return structuredClone(proposalRecord.output);}throw new ServiceError('EVIDENCE_VALIDATION','Probe stops at avoidable source repair');},verify:async(p)=>{verifications++;source=p.facts[10].sources.find(s=>s.index===12)?.quote;throw new ServiceError('EVIDENCE_VALIDATION','Probe stops before semantic model; no acceptance is asserted');}};
let errorCode;
try{await new Extractor(configFromEnv({MEMORY_MODE:'enhanced',MEMORY_MAX_REPAIR_ROUNDS:'2'}),model).prepare(req,snapshot,AbortSignal.timeout(2000));}catch(error){errorCode=error.code;}
console.log(JSON.stringify({protocol:'recorded-source-copy-regression-v1',runtime,parent_results_sha256:sha(readFileSync(join(parent,'results.json'))),proposal_sha256:sha(JSON.stringify(proposalRecord.output)),generations,verifications,recovered_source:source,error_code:errorCode,scope:'Fixed full-context extraction output; model stubs stop before semantic judgment. No write, live model latency or pipeline acceptance claim.'}));
assert.equal(generations,1);assert.equal(verifications,1);assert.equal(source,"he doesn't even notice");assert.equal(errorCode,'EVIDENCE_VALIDATION');
