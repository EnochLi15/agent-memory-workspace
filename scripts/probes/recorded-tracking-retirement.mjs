// Exact saved request/proposal, no model call and no mutation of a tenant.
import {readFileSync,writeFileSync,mkdtempSync} from 'node:fs';
import {resolve,join} from 'node:path';import {createHash} from 'node:crypto';import assert from 'node:assert/strict';
import * as old from '../../artifacts/round2-runtime-d8e264b/dist/operation-intent.js';
import * as current from '../../service/dist/operation-intent.js';
const snapshotPath=resolve(process.argv[2]),tracePath=resolve(process.argv[3]);
const sha=x=>createHash('sha256').update(x).digest('hex');
const saved=JSON.parse(readFileSync(snapshotPath,'utf8'));
const proposal=readFileSync(join(tracePath,'model-proposals.jsonl'),'utf8').trim().split('\n').map(JSON.parse).find(r=>r.purpose==='extraction').output;
const result=mod=>({obligations:mod.forgetObligations(saved.request).map(x=>({index:x.index,quote:x.span.quote})),operations:proposal.operations.map(o=>({type:o.type,quote:o.source.quote,authorized:mod.authorizesForget(o,saved.request)}))});
const before=result(old),after=result(current);
assert.equal(before.operations.length,1);assert.equal(before.operations[0].authorized,false);assert.equal(after.operations[0].authorized,true);
assert.equal(before.obligations.length,0);assert.equal(after.obligations.length,1);
const dir=mkdtempSync(resolve('artifacts/round2-recorded-tracking-retirement-'));
const paths=[snapshotPath,join(tracePath,'model-proposals.jsonl'),'artifacts/round2-runtime-d8e264b/src/operation-intent.ts','service/src/operation-intent.ts'];
const report={protocol:'recorded-tracking-retirement-guard-v1',run_dir:dir,source_sha256:sha(readFileSync(import.meta.filename)),input_hashes:Object.fromEntries(paths.map(p=>[p,sha(readFileSync(p))])),before,after,new_model_calls:0,scope:'Deterministic authorization/omission guard comparison on the exact first proposal of a saved failed checkpoint diagnostic. No fresh model output, HTTP, commit, full-prefix recovery or benchmark score. The original full-run proposal was not logged.'};
writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');
writeFileSync('reports/round2-recorded-tracking-retirement.json',JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report));
