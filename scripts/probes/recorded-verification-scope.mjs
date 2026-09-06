// Offline dependency-invalidation replay of an archived real verifier response.
// No new model call or score: only validates what a subsequent call must recheck.
import {readFileSync,writeFileSync,readdirSync,mkdtempSync} from 'node:fs';
import {join,resolve} from 'node:path';import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {VerificationSession} from '../../service/dist/verification-session.js';
import {verificationInput} from '../../service/dist/verification.js';
import {extractionSchema} from '../../service/dist/types.js';
const sha=x=>createHash('sha256').update(x).digest('hex');
const parent=resolve(process.argv[2]??'artifacts/round2-failure-prefixes/2026-09-06T03-14-50-774Z-tZoQwl');
const read=p=>JSON.parse(readFileSync(p,'utf8'));
const lines=p=>readFileSync(p,'utf8').trim().split('\n').map(x=>JSON.parse(x));
const prior=read(join(parent,'results.json')),row=prior.cases[0];
assert.equal(row.status,'failed');
const inputs=lines(join(parent,'model-inputs.jsonl')).filter(r=>r.purpose==='verification').slice(-2);
const before=JSON.parse(inputs[0].input),after=JSON.parse(inputs[1].input);
assert.deepEqual(before.NEW_MESSAGES,after.NEW_MESSAGES);
const recorded=lines(join(parent,'extraction-proposals.jsonl')).find(r=>r.input_sha256===inputs[0].input_sha256);assert.ok(recorded);
const requests=lines('eval/artifacts/holdout-v2-U3-memops/requests.jsonl');
const original=requests.find(r=>r.body?.request_id===row.adds.at(-1).original_request_id).body;
const req={...original,user_id:row.user_id,request_id:row.user_id+':'+(row.adds.length-1)};
assert.deepEqual(req.messages,before.NEW_MESSAGES.map(({index,...m})=>m));
const existing=read(join(parent,row.user_id.split(':').at(-1)+'-state.json')).facts;
// Reconstruct prepare()'s ephemeral same-chunk records; validate IDs and content
// against archived target context before relying on the reconstructed pool.
const pool=p=>[...existing,...p.facts.map((f,i)=>({...f,id:sha(`${req.user_id}\0${req.request_id}\0fact\0${i}`),source_ids:[],source_quotes:f.sources.map(s=>s.quote),created_at:'',observed_at:'',state:'active',vector:null,entities:[],revision:0}))];
const p1=extractionSchema.parse(before.PROPOSAL),p2=extractionSchema.parse(after.PROPOSAL);
for(const [data,p] of [[before,p1],[after,p2]])for(const t of data.TARGET_FACTS){const actual=pool(p).find(f=>f.id===t.id);assert.ok(actual);for(const [k,v] of Object.entries(t))assert.deepEqual(actual[k],v);}
const session=new VerificationSession(),identity={protocol:'offline-archived-verdict-replay-v1'};
const first=session.plan(req,p1,pool(p1),identity),findings=session.evaluate(first,recorded.output);assert.ok(findings.length);
const second=session.plan(req,p2,pool(p2),identity);assert.equal(second.blockedFindings.length,0);
const scoped=verificationInput(req,p2,pool(p2),after.OMISSION_HINTS,second.scope);
assert.equal(scoped.NEW_MESSAGES.length,req.messages.length);assert.equal(scoped.PROPOSAL.facts.length,p2.facts.length);
const count=s=>s.fact_indices.length+s.operation_indices.length+s.replacements.length+s.message_indices.length;
const report={protocol:'offline-archived-verdict-replay-v1',parent,source_sha256:Object.fromEntries(readdirSync('service/src').filter(f=>f.endsWith('.ts')).map(f=>['service/src/'+f,sha(readFileSync('service/src/'+f))])),probe_sha256:sha(readFileSync(import.meta.filename)),parent_results_sha256:sha(readFileSync(join(parent,'results.json'))),first_input_sha256:inputs[0].input_sha256,patched_input_sha256:inputs[1].input_sha256,recorded_response_sha256:sha(JSON.stringify(recorded.output)),findings,full_scope:first.scope,patched_scope:second.scope,total_checks:count(first.scope),fresh_checks:count(second.scope),reused_checks:second.reused,full_messages:scoped.NEW_MESSAGES.length,full_facts:scoped.PROPOSAL.facts.length,changed_fact_indices:p1.facts.flatMap((f,i)=>JSON.stringify(f)!==JSON.stringify(p2.facts[i])?[i]:[]),limitations:'Offline mechanism replay of archived verdicts; certificates exist only in this isolated probe. No live verification, no measured latency saving, no full-prefix pass or accuracy claim.'};
const output=mkdtempSync(resolve('artifacts/round2-recorded-verification-scope-'));writeFileSync(join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
writeFileSync('reports/round2-recorded-C30-verification-scope.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({output,total:report.total_checks,fresh:report.fresh_checks,reused:report.reused_checks,scope:report.patched_scope}));
