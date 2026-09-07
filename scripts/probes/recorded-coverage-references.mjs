// Replay the original C30 checker output at Models.verify; no API or verdict resampling.
import {readFileSync} from 'node:fs';import {resolve,join} from 'node:path';import {pathToFileURL} from 'node:url';import {createHash} from 'node:crypto';import assert from 'node:assert/strict';
const runtime=resolve(process.argv[2]??'service'),parent=resolve('artifacts/round2-failure-prefixes/2026-09-06T04-22-30-009Z-wCPvSm');
const read=p=>JSON.parse(readFileSync(p,'utf8')),lines=p=>readFileSync(p,'utf8').trim().split('\n').map(JSON.parse),sha=x=>createHash('sha256').update(x).digest('hex');
const {Models}=await import(pathToFileURL(join(runtime,'dist/models.js'))),{configFromEnv}=await import(pathToFileURL(join(runtime,'dist/config.js'))),{VerificationSession}=await import(pathToFileURL(join(runtime,'dist/verification-session.js')));
const parentReport=read(join(parent,'results.json')),row=parentReport.cases[0];
const record=lines(join(parent,'extraction-proposals.jsonl')).filter(x=>Array.isArray(x.output?.message_checks)).at(-1),input=JSON.parse(record.input);
const original=lines('eval/artifacts/holdout-v2-U3-memops/requests.jsonl').find(x=>x.body?.request_id===row.adds.at(-1).original_request_id).body;
const req={...original,user_id:row.user_id,request_id:row.user_id+':'+(row.adds.length-1)};
assert.deepEqual(req.messages,input.NEW_MESSAGES.map(({index,...message})=>message));
const proposal=input.PROPOSAL,facts=read(join(parent,'C30_update-state.json')).facts;
assert.deepEqual(input.TARGET_FACTS,[]);
const model=new Models(configFromEnv({MEMORY_VERIFICATION_FORMAT:'compact'})),session=new VerificationSession();let calls=0;
model.json=async()=>{calls++;return structuredClone(record.output);};
const findings=await model.verify(proposal,req,facts,[],AbortSignal.timeout(1000),session);
const unchanged=await model.verify(proposal,req,facts,[],AbortSignal.timeout(1000),session);
const expected=[2,4,6],actual=findings.map(x=>Number(x.match(/^message (\d+):/)?.[1]));
console.log(JSON.stringify({protocol:'recorded-coverage-reference-regression-v1',runtime,parent_report_sha256:sha(readFileSync(join(parent,'results.json'))),record_sha256:sha(JSON.stringify(record)),generation_calls:calls,expected_missing_messages:expected,actual_missing_messages:actual,findings,unchanged_rejection_preserved:JSON.stringify(unchanged)===JSON.stringify(findings)}));
assert.equal(calls,1);assert.deepEqual(actual,expected,'Checker citation error must not be reported or cached as a semantic memory omission');assert.deepEqual(unchanged,findings);
