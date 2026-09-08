// Reproduce the failed request using copied state and one extraction worker.
// Preparation only: never commit into either historical or copied state.
import {readFileSync,writeFileSync,mkdirSync,cpSync,existsSync} from 'node:fs';
import {resolve,join} from 'node:path';import {createHash} from 'node:crypto';
import {Extractor} from '../../service/dist/extraction.js';import {Models} from '../../service/dist/models.js';
import {configFromEnv} from '../../service/dist/config.js';import {TenantStore} from '../../service/dist/storage.js';
const root=resolve(import.meta.dirname,'../..'),run='priority-full-conv30-20260908-01-candidate';
const dir=join(root,'artifacts/priority-conv30-single-worker-20260908');
if(existsSync(dir))throw Error('Preserve previous diagnostic output');mkdirSync(dir);
const requests=readFileSync(join(root,'eval/artifacts',run+'-locomo/requests.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
const req=requests.find(r=>r.body?.request_id===run+':locomo:conv-30:session-13:0').body;
const tenant=createHash('sha256').update(req.user_id).digest('hex'),copy=join(dir,'state');mkdirSync(copy);
cpSync(join(root,'service/.data',run,tenant),join(copy,tenant),{recursive:true});
const spec=JSON.parse(readFileSync(join(root,'configs/priority-benchmarks-run-20260908.json'),'utf8'));
process.env.MEMORY_MODEL_TRACE=join(dir,'model-trace.jsonl');
const cfg=configFromEnv({...process.env,...spec.defaults,MEMORY_EXTRACTION_WORKERS:'1',MEMORY_DATA_DIR:copy});
const store=new TenantStore(copy,req.user_id),models=new Models(cfg),verify=models.verify.bind(models),checks=[];
models.verify=async(...args)=>{const findings=await verify(...args);checks.push(findings);return findings;};
const result={request_id:req.request_id,extraction_workers:1,mode:'isolated_prepare_no_commit',checks},start=Date.now();
try{const p=await new Extractor(cfg,models).prepare(req,store.snapshot(req.session_id),AbortSignal.timeout(cfg.addTimeout));Object.assign(result,{status:'prepared',facts:p.facts.length,degraded:p.degraded});}
catch(e){Object.assign(result,{status:'failed',error:e.message,code:e.code});}
finally{store.close();}
result.elapsed_ms=Date.now()-start;writeFileSync(join(dir,'result.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));
