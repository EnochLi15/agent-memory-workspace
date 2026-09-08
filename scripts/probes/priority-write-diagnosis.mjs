// Isolated preparation only: copied tenant state, no commit and no benchmark score.
import {readFileSync,writeFileSync,mkdirSync,cpSync,existsSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {createHash} from 'node:crypto';
import {Extractor} from '../../service/dist/extraction.js';
import {Models} from '../../service/dist/models.js';
import {configFromEnv} from '../../service/dist/config.js';
import {TenantStore} from '../../service/dist/storage.js';

const root=resolve(import.meta.dirname,'../..');
const run='priority-20260908-02-candidate';
const dir=join(root,'artifacts/priority-write-diagnosis-20260908');
mkdirSync(dir,{recursive:true});
const requests=readFileSync(join(root,'eval/artifacts',run+'-locomo/requests.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
const spec=JSON.parse(readFileSync(join(root,'configs/priority-benchmarks-run-20260908.json'),'utf8'));
const results=[];
for(const sample of ['conv-42','conv-43']){
 const session=sample==='conv-42'?'session-3':'session-2';
 const request=requests.find(r=>r.path==='/add'&&r.body.request_id===`${run}:locomo:${sample}:${session}:0`).body;
 const trace=join(dir,sample+'-model-trace.jsonl');
 if(existsSync(trace))throw Error('Preserve prior diagnosis traces; do not overwrite');
 const tenant=createHash('sha256').update(request.user_id).digest('hex');
 const copy=join(dir,sample+'-state');
 mkdirSync(copy,{recursive:true});
 cpSync(join(root,'service/.data',run,tenant),join(copy,tenant),{recursive:true});
 process.env.MEMORY_MODEL_TRACE=trace;
 const config=configFromEnv({...process.env,...spec.defaults,MEMORY_DATA_DIR:copy});
 const store=new TenantStore(copy,request.user_id);
 const models=new Models(config),verify=models.verify.bind(models),checks=[];
 models.verify=async(...args)=>{
  try{const findings=await verify(...args);checks.push({findings});return findings;}
  catch(e){checks.push({error:e.message,code:e.code});throw e;}
 };
 const started=Date.now();const result={sample,request_id:request.request_id,mode:'isolated_preparation_no_commit',checks};
 try{
  const prepared=await new Extractor(config,models).prepare(request,store.snapshot(session),AbortSignal.timeout(config.addTimeout));
  Object.assign(result,{status:'prepared',facts:prepared.facts.length,degraded:prepared.degraded});
 }catch(e){Object.assign(result,{status:'failed',code:e.code,error:e.message});}
 finally{store.close();}
 result.elapsed_ms=Date.now()-started;results.push(result);
 writeFileSync(join(dir,'results.json'),JSON.stringify(results,null,2)+'\n',{mode:0o600});
 console.log(JSON.stringify(result));
}
