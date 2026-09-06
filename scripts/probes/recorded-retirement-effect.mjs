// Exact saved accepted proposal, no model calls and no database mutation.
import {readFileSync,writeFileSync} from 'node:fs';import {createHash} from 'node:crypto';import {resolve,join} from 'node:path';import {pathToFileURL} from 'node:url';import assert from 'node:assert/strict';
const parent=resolve('artifacts/round2-failure-prefixes/2026-09-06T05-04-39-239Z-Qf4JfS');
const bytes=readFileSync(join(parent,'extraction-proposals.jsonl'));
const rows=bytes.toString().trim().split('\n').map(JSON.parse),record=rows.findLast(r=>JSON.parse(r.input).CHECK_SCOPE);
const input=JSON.parse(record.input),req={request_id:'recorded',user_id:'round2-prefix:D17_remember',session_id:'recorded',messages:input.NEW_MESSAGES.map(({index,...m})=>m)};
const sha=x=>createHash('sha256').update(x).digest('hex'),results=[];
for(const commit of ['ef8d236','4bca000']){
 const runtime=resolve('artifacts/round2-runtime-'+commit),{missingForgetObligations}=await import(pathToFileURL(join(runtime,'dist/operation-intent.js')).href);
 const missing=missingForgetObligations(req,input.PROPOSAL);
 results.push({commit,missing_count:missing.length,missing:missing.map(({index,span})=>({index,quote:span.quote})),source_sha256:sha(readFileSync(join(runtime,'src/operation-intent.ts')))});
}
assert.equal(results[0].missing_count,0);assert.equal(results[1].missing_count,1);assert.equal(results[1].missing[0].index,16);
const report={protocol:'recorded-retirement-effect-guard-v1',parent_dir:parent,parent_records_sha256:sha(bytes),verification_input_sha256:sha(record.input),proposal_sha256:sha(JSON.stringify(input.PROPOSAL)),probe_sha256:sha(readFileSync(import.meta.filename)),results,scope:'Exact already accepted D17 proposal at the obligation guard: old code treats retract as satisfying erasure, new code reports the missing effect. No new extraction, semantic verdict, HTTP write or benchmark score.'};
writeFileSync('reports/round2-recorded-retirement-effect.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
