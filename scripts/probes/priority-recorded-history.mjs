// Full frozen-store retrieval reproduction; no new writes or benchmark rejudging.
import assert from 'node:assert/strict';
import {readFileSync,writeFileSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
const root=resolve('.'),candidate=join(root,'artifacts/priority-quality-history-20260908');
const old=join(root,'artifacts/priority-quality-fix-20260908/service-dist'),next=join(candidate,'service-dist');
const sha=b=>createHash('sha256').update(b).digest('hex');
const manifest=JSON.parse(readFileSync(join(candidate,'code-snapshot.json')));
for(const [name,digest] of Object.entries(manifest.dist_hashes))assert.equal(sha(readFileSync(join(next,name))),digest);
const {Models}=await import(pathToFileURL(join(next,'models.js')));
const {TenantStore}=await import(pathToFileURL(join(next,'storage.js')));
const before=await import(pathToFileURL(join(old,'retrieval.js'))),after=await import(pathToFileURL(join(next,'retrieval.js')));
const data=JSON.parse(readFileSync('eval/.data/priority-expansion-20260908/memops-four-operations-20.json'));
const sample=data.find(s=>s.sample_id==='B01_update'),question=sample.questions.find(q=>q.qid.includes('#p1_operation_trace#'));
const req={user_id:'priority-memops-four-20260908-01-candidate:memops:B01_update',query:question.question,top_k:100};
const config=JSON.parse(readFileSync('artifacts/priority-memops-four-20260908-01/candidate-service.json'));
config.dataDir=join(root,'artifacts/priority-quality-fix-20260908/memops-state');
const store=new TenantStore(config.dataDir,req.user_id);
try{
 const vector=(await new Models(config).embedBatch([req.query],'search',AbortSignal.timeout(20000)))[0];
 const results={};
 for(const [label,mod] of [['before',before],['after',after]]){
  const frame=mod.collectCandidates(store,req,vector,config),response=mod.packEvidence(store,req,config,frame);
  const relevant=response.data.filter(m=>/\$145|14\.5|15\.2/.test(m.content));
  results[label]={returned:response.data.length,early_salary_evidence:relevant,memories:response.data};
 }
 assert.equal(results.before.early_salary_evidence.length,0,'Recorded missing evidence must reproduce');
 assert.ok(results.after.early_salary_evidence.length>0,'Fixed full-store query must retrieve the original corrected report');
 assert.match(results.after.early_salary_evidence.map(m=>m.content).join('\n'),/later corrected/);
 writeFileSync(join(candidate,'recorded-history-retrieval.json'),JSON.stringify({qid:question.qid,query:question.question,candidate_manifest_sha256:sha(readFileSync(join(candidate,'code-snapshot.json'))),scope:'Same query vector and cloned full store in both retrieval versions; no add or model generation.',...results},null,2)+'\n');
 console.log(JSON.stringify({before:results.before.early_salary_evidence.length,after:results.after.early_salary_evidence.length,early_evidence:results.after.early_salary_evidence}));
}finally{store.close();}
