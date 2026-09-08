// One targeted follow-up using the standard evaluator and an isolated snapshot.
import assert from 'node:assert/strict';
import {readFileSync,writeFileSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import {parseEnv} from 'node:util';
import {execFileSync} from 'node:child_process';
const root=resolve('.'),artifact=join(root,'artifacts/priority-quality-history-20260908');
const reportOnly=process.argv.includes('--report-only');
const sha=x=>createHash('sha256').update(x).digest('hex');
const read=p=>JSON.parse(readFileSync(p)),rows=p=>readFileSync(p,'utf8').trim().split('\n').filter(Boolean).map(JSON.parse);
const frozen=read(join(artifact,'code-snapshot.json'));
for(const [name,digest] of Object.entries(frozen.dist_hashes))assert.equal(sha(readFileSync(join(artifact,'service-dist',name))),digest);
const origin=read(join(root,'eval/artifacts/priority-memops-four-20260908-01-candidate-memops/manifest.json'));
const config=read(join(root,'artifacts/priority-memops-four-20260908-01/candidate-service.json'));
config.dataDir=join(artifact,'state');config.port=0;config.candidate_execution_artifact=frozen;
const safe=JSON.stringify(config);if(!reportOnly)writeFileSync(join(artifact,'service-config.json'),safe+'\n');
process.env.SERVICE_CONFIG_JSON=safe;process.env.SERVICE_CONFIG_SHA256=sha(safe);
process.env.MEMORY_MODEL_AUDIT=join(artifact,'qa-model-calls.jsonl');
process.env.MEMORY_RETRIEVAL_AUDIT=join(artifact,'qa-retrieval-stages.jsonl');
if(!reportOnly)config.llmKey=parseEnv(readFileSync(join(root,'.env'),'utf8')).MEMORY_LLM_API_KEY;
const {buildServer}=await import(pathToFileURL(join(artifact,'service-dist/server.js')));
const {run}=await import(pathToFileURL(join(root,'eval/dist/runner.js')));
let app;const runId='priority-history-followup-20260908-01-candidate-memops';
const runDir=join(root,'eval/artifacts',runId);
try{
 if(!reportOnly){
 app=await buildServer(config);const baseUrl=await app.listen({host:'127.0.0.1',port:0});
 process.chdir(join(root,'eval'));
 await run({baseUrl,runDir,runId,dataFile:join(artifact,'input.json'),memoryNamespace:origin.memory_namespace,
  limit:0,concurrency:1,mode:origin.mode,answerModel:origin.answer_model,judgeModel:origin.judge_model,
  llmBase:config.llmBase,llmKey:config.llmKey,judgeBase:origin.judge_base,judgeKey:config.llmKey,judgeKind:origin.judge_kind,
  maxMessages:origin.chunk_messages,maxWords:origin.chunk_words,topK:origin.top_k,resume:false});
 }
 const manifest=read(join(runDir,'manifest.json'));
 assert.equal(manifest.status,'finished');
 for(const key of ['answer_model','judge_model','judge_kind','answer_prompt_sha256','rubric_prompt_sha256','top_k','chunk_messages','chunk_words'])assert.equal(manifest[key],origin[key]);
 let equivalence={kind:'same_commit_and_patch',baseline_commit:origin.eval_commit,candidate_commit:manifest.eval_commit};
 if(manifest.eval_commit===origin.eval_commit)assert.equal(manifest.source_state.eval.patch_sha256,origin.source_state.eval.patch_sha256);
 else{
  const committedPatch=execFileSync('git',['diff',origin.eval_commit,manifest.eval_commit],{cwd:join(root,'eval')});
  assert.equal(sha(committedPatch),origin.source_state.eval.patch_sha256,'Committed evaluator content differs from the baseline working tree');
  assert.equal(manifest.source_state.eval.patch_sha256,sha(''),'Candidate evaluator has additional changes');
  equivalence={...equivalence,kind:'baseline_working_tree_patch_committed_verbatim',patch_sha256:sha(committedPatch)};
 }
 const calls=rows(process.env.MEMORY_MODEL_AUDIT);assert.ok(calls.every(r=>r.kind!=='generation'),'Receipt replay must not write with a model');
 const verdicts=rows(join(runDir,'judgments.jsonl'));assert.equal(verdicts.length,1);
 const report={run_id:runId,scope:'One targeted follow-up after the quoted-statement fix, on unchanged full B01_update history; not substituted into the completed 92-question v1 score.',candidate:frozen,evaluator_equivalence:equivalence,
  verdict:verdicts[0],prediction:rows(join(runDir,'predictions.jsonl'))[0],metrics:read(join(runDir,'metrics.json')),
  http_receipts:rows(join(runDir,'ingest.jsonl')).length,write_model_calls:0};
 writeFileSync(join(root,'reports/priority-history-followup-20260908.json'),JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify({run_id:runId,verdict:verdicts[0]}));
}finally{if(app)await app.close();}
