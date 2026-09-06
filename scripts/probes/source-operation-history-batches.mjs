// Large synthetic history is seeded deterministically. Only the rejection request
// uses real extraction, source review, verification and local embeddings.
import {readFileSync,writeFileSync,mkdtempSync,readdirSync,mkdirSync,copyFileSync} from 'node:fs';
import {resolve,join} from 'node:path';import {parseEnv} from 'node:util';import {createHash} from 'node:crypto';
import {configFromEnv} from '../../service/dist/config.js';import {Extractor,hash} from '../../service/dist/extraction.js';
import {Models} from '../../service/dist/models.js';import {TenantStore} from '../../service/dist/storage.js';
import {sourceOperationWork} from '../../service/dist/source-operations.js';
import {sourceOperationBatches,sourceOperationNeedsBatches} from '../../service/dist/source-operation-batches.js';
const sha=x=>createHash('sha256').update(x).digest('hex'),dir=mkdtempSync(resolve('artifacts/round2-history-batches-live-'));
const resume=process.argv.find(a=>a.startsWith('--resume-error='))?.slice('--resume-error='.length);
const specPath='configs/round2-quality-source-operation-batches.json',spec=JSON.parse(readFileSync(specPath));
const config={...configFromEnv({...parseEnv(readFileSync('.env','utf8')),...spec.defaults}),dataDir:join(dir,'data')};
process.env.MEMORY_MODEL_TRACE=join(dir,'private-model-trace.jsonl');process.env.MEMORY_MODEL_AUDIT=join(dir,'model-usage.jsonl');
mkdirSync(join(dir,'source-snapshot'));const files=readdirSync('service/src').filter(f=>f.endsWith('.ts'));for(const f of files)copyFileSync('service/src/'+f,join(dir,'source-snapshot',f));copyFileSync(import.meta.filename,join(dir,'probe-source.mjs'));
const timestamp='2026-01-01T00:00:00Z',user_id='history-batches-control';
const contents=Array.from({length:160},(_,i)=>'Background astronomy topic '+i+'. '+'Galaxies are observable in the sky. '.repeat(55));
contents[0]='I use Firefox.';contents[1]='Your gym is Peak Gym. Water is available.';contents[70]='My brother goes to Peak Gym.';contents[130]='Peak Gym was my incorrect claim. Bring water.';
const seed={user_id,request_id:'seed',session_id:'s',messages:contents.map((content,i)=>({role:i===0||i===70?'user':'assistant',content,timestamp}))};
const request={user_id,request_id:'reject',session_id:'s',messages:[{role:'user',content:"Your earlier claim about MY gym is wrong. Please don't store that. Keep my brother's real gym and my browser preference.",timestamp}]};
writeFileSync(join(dir,'requests.json'),JSON.stringify({seed,request})+'\n');
const report={protocol:'history-batches-live-v1',run_dir:dir,scope:'Deterministic synthetic 160-message seed; real model-backed rejection prepare and atomic commit. No HTTP ingestion baseline, benchmark answers or accuracy claim.',source_sha256:Object.fromEntries(files.map(f=>[f,sha(readFileSync('service/src/'+f))])),spec_sha256:sha(readFileSync(specPath)),requests_sha256:sha(readFileSync(join(dir,'requests.json'))),started_at:new Date().toISOString(),status:'running',checks:[]};
const save=()=>writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');save();console.log(JSON.stringify({run_dir:dir,status:report.status}));
let store=new TenantStore(config.dataDir,user_id),before;
try{
 const models={json:async()=>({facts:[{content:'I use Firefox.',subject:'user',predicate:'browser',value:'Firefox',sources:[{index:0,quote:contents[0]}]},{content:contents[70],subject:"user's brother",predicate:'gym',value:'Peak Gym',sources:[{index:70,quote:contents[70]}]}],operations:[]}),verify:async()=>[],embedBatch:async(xs)=>xs.map(()=>Array.from({length:768},(_,i)=>i===0?1:0))};
 const prepared=await new Extractor(config,models).prepare(seed,store.snapshot('s'),AbortSignal.timeout(10000));store.commit(seed,hash(JSON.stringify(seed)),prepared,0);
 before=store.snapshot('s');const work=sourceOperationWork(request,before.facts,before.erasureSources),partition=sourceOperationBatches(work);
 report.source_count=work.sources.length;report.source_chars=work.sources.reduce((n,s)=>n+s.content.length,0);report.batches=partition.batches.length;report.requires_batches=sourceOperationNeedsBatches(work);save();
 const start=performance.now();let result;
 const liveModels=new Models(config);
 if(resume){
  const prior=JSON.parse(readFileSync(join(resume,'report.json'))),trace=readFileSync(join(resume,'private-model-trace.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
  if(prior.status!=='failed'||prior.error?.code!=='VERIFICATION_UNAVAILABLE'||prior.failure_left_state_unchanged!==true||prior.spec_sha256!==report.spec_sha256||prior.requests_sha256!==report.requests_sha256||JSON.stringify(prior.source_sha256)!==JSON.stringify(report.source_sha256))throw Error('Transport resume identity or failure mismatch');
  const successes=new Map(trace.filter(r=>r.outcome==='ok').map(r=>[sha(JSON.stringify([r.purpose,r.system,r.input])),r.output]));
  const invoke=liveModels.json.bind(liveModels);report.transport_resume={parent:resolve(resume),parent_report_sha256:sha(readFileSync(join(resume,'report.json'))),cached_successes:successes.size,reused:0,scope:'Exact successful stage responses replayed; only unfinished/transport-failed calls are sent again. New preparation timing excludes parent cost; this is not an independent full repeat.'};
  liveModels.json=async(prompt,input,signal,context)=>{const key=sha(JSON.stringify([context?.purpose,prompt,input]));if(successes.has(key)){report.transport_resume.reused++;return structuredClone(successes.get(key));}return invoke(prompt,input,signal,context);};
 }
 try{result=await new Extractor(config,liveModels).prepare(request,before,AbortSignal.timeout(config.addTimeout));}finally{report.prepare_ms=performance.now()-start;}
 writeFileSync(join(dir,'prepared.json'),JSON.stringify(result)+'\n');store.commit(request,hash(JSON.stringify(request)),result,before.revision);store.close();store=new TenantStore(config.dataDir,user_id);
 const after=store.snapshot('s'),all=JSON.stringify(after.erasureSources),active=after.facts.filter(f=>f.state==='active');
 for(const [name,pass] of [['actual_multibatch_review',report.requires_batches&&result.sourceOperationPlan.batch_review.screenings.length===partition.batches.length&&result.sourceOperationPlan.batch_review.closures.length===partition.batches.length],['target_and_echo_removed',!/Your gym is Peak Gym|Peak Gym was my incorrect/.test(all)],['brother_same_name_preserved',active.some(f=>/brother/.test(f.subject)&&f.value==='Peak Gym')&&all.includes(contents[70])],['browser_preserved',active.some(f=>f.value==='Firefox')],['independent_neighbors_preserved',all.includes('Water is available.')&&all.includes('Bring water.')],['single_commit_persists_after_reopen',after.revision===before.revision+1]])report.checks.push({name,pass});
 report.status=report.checks.every(c=>c.pass)?'passed':'failed_checks';
}catch(e){report.status='failed';report.error={code:e.code??null,message:e.message};if(before)report.failure_left_state_unchanged=sha(JSON.stringify(before))===sha(JSON.stringify(store.snapshot('s')));}
finally{store.close();report.finished_at=new Date().toISOString();save();writeFileSync('reports/'+dir.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));}
