// Paired verifier-only comparison on one exposed, fixed failed proposal.
// Does not retry toward a preferred verdict, repair facts, or publish memory.
import {readFileSync,writeFileSync,mkdtempSync} from 'node:fs';
import {resolve,join} from 'node:path';import {pathToFileURL} from 'node:url';
import {parseEnv} from 'node:util';import {createHash} from 'node:crypto';
const runtime=resolve('artifacts/round2-runtime-8cb099f'),parent=resolve('artifacts/round2-LoCoMo-87d67b1-repair-failure-elj99ij1');
const control=resolve('artifacts/round2-proposal-index-control-Uh4Bb7/private-verification-input.json');
const {Models}=await import(pathToFileURL(join(runtime,'dist/models.js')));
const {configFromEnv}=await import(pathToFileURL(join(runtime,'dist/config.js')));
const {extractionSchema}=await import(pathToFileURL(join(runtime,'dist/types.js')));
const {decodeCompactVerification}=await import(pathToFileURL(join(runtime,'dist/verification-compact.js')));
const {verificationIssues}=await import(pathToFileURL(join(runtime,'dist/verification.js')));
const {sourceCoverageWork}=await import(pathToFileURL(join(runtime,'dist/source-coverage.js')));
const old=readFileSync(join(parent,'private-model-trace.jsonl'),'utf8').trim().split('\n').map(JSON.parse).find(r=>r.purpose==='verification'&&r.outcome==='ok');
const labeled=JSON.parse(readFileSync(control,'utf8')),data=JSON.parse(old.input),stripped=JSON.parse(labeled.input);
for(const f of stripped.PROPOSAL.facts)delete f.fact_index;for(const o of stripped.PROPOSAL.operations)delete o.operation_index;
if(JSON.stringify(stripped)!==JSON.stringify(data))throw Error('Pair changes input beyond labels');
const labelInstruction='Use the server-supplied fact_index and operation_index labels in PROPOSAL; these are zero-based positions in the full current arrays. Copy the label of the exact item being judged, and ensure each reason describes that same item. Labels are input metadata, never evidence or editable fact fields. ';
if(labeled.system.replace(labelInstruction,'')!==old.system)throw Error('Pair changes semantics beyond index instruction');
const spec=JSON.parse(readFileSync(join(parent,'spec.json'),'utf8'));
const config=configFromEnv({...parseEnv(readFileSync('.env','utf8')),...spec.defaults});
const model=new Models(config),req=JSON.parse(readFileSync(join(parent,'request.json'),'utf8'));
// Verification may receive source-filtered messages; match the saved input exactly.
req.messages=data.NEW_MESSAGES.map(({index,...message})=>message);
const proposal=extractionSchema.parse(data.PROPOSAL),coverage=sourceCoverageWork(req,proposal);
const sha=x=>createHash('sha256').update(x).digest('hex'),dir=mkdtempSync(resolve('artifacts/round2-verification-index-repeat-'));
process.env.MEMORY_MODEL_TRACE=join(dir,'private-model-trace.jsonl');process.env.MEMORY_MODEL_AUDIT=join(dir,'model-calls.jsonl');
const report={protocol:'fixed-proposal-index-paired-verification-v1',run_dir:dir,started_at:new Date().toISOString(),live:process.argv.includes('--live'),planned_calls:6,order:[['original','labeled'],['labeled','original'],['original','labeled']],model:config.llmStageModels.verification??config.llmModel,effort:config.llmReasoningEffort,input_hashes:{original:sha(old.input),labeled:sha(labeled.input)},system_hashes:{original:sha(old.system),labeled:sha(labeled.system)},rows:[],scope:'Three preplanned paired verifier-only repeats on one exposed proposal. Every outcome retained, no repair or storage. Verdict consistency is not semantic correctness or benchmark accuracy. Shared gateway timings may be contended.'};
writeFileSync(join(dir,'probe-source.mjs'),readFileSync(import.meta.filename));
const save=()=>{writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');writeFileSync('reports/'+dir.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');};save();
console.log(JSON.stringify({run_dir:dir,live:report.live,planned_calls:report.planned_calls}));
if(report.live){
 for(const [round,order] of report.order.entries())for(const variant of order){
  const packet=variant==='original'?old:labeled,row={round:round+1,variant,started_at:new Date().toISOString()},started=performance.now();report.rows.push(row);save();
  try{
   const raw=await model.json(packet.system,packet.input,AbortSignal.timeout(95000),{purpose:'verification',trace:{user_id:req.user_id,request_id:`index-repeat:${round+1}:${variant}`}});
   const decoded=decodeCompactVerification(raw,proposal,data.CHECK_SCOPE,coverage);
   row.protocol_errors=decoded.protocolErrors;row.semantic_findings=verificationIssues(decoded.canonical,req,proposal,coverage);
   row.neighbor_checks=raw.fact_checks.filter(c=>c[0]===62||c[0]===63);row.status=row.protocol_errors.length?'invalid_protocol':'validated';
  }catch(error){row.status='failed';row.error={code:error.code??null,message:error.message};}
  row.elapsed_ms=performance.now()-started;save();console.log(JSON.stringify(row));
 }
 report.finished_at=new Date().toISOString();report.completed_calls=report.rows.length;save();
}
