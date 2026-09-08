// Deterministic ingest regression: real extractor/store, isolated tenants, no
// model calls or judge scores. Every scheduled write is attempted and counted.
import {readFileSync,writeFileSync,mkdtempSync,rmSync,existsSync,mkdirSync} from 'node:fs';
import {resolve,join,dirname} from 'node:path';
import {tmpdir} from 'node:os';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {chunks} from '../../eval/dist/adapters.js';
const args=process.argv.slice(2);
const arg=(name,fallback)=>{const i=args.indexOf('--'+name);return i<0?fallback:args[i+1];};
const root=resolve(arg('service-root',fileURLToPath(new URL('../../service',import.meta.url))));
const dataset=resolve(arg('dataset','eval/.data/priority-20260908-v1/memops.json'));
const output=resolve(arg('output','artifacts/offline-replay.json'));
if(existsSync(output))throw Error('Preserve the previous replay report; choose a new output path');
mkdirSync(dirname(output),{recursive:true});
const load=name=>import(pathToFileURL(join(root,'dist',name+'.js')));
const {Extractor,hash}=await load('extraction');
const {TenantStore}=await load('storage');
const {configFromEnv}=await load('config');
const bytes=readFileSync(dataset),samples=JSON.parse(bytes);
const maxMessages=Number(arg('chunk-messages','20')),maxWords=Number(arg('chunk-words','2000'));
if(!Number.isInteger(maxMessages)||maxMessages<1||!Number.isInteger(maxWords)||maxWords<1)throw Error('Invalid chunk limits');
const dir=mkdtempSync(join(tmpdir(),'offline-regression-'));
const result={kind:'offline_ingest_regression_no_model_no_judge',dataset_sha256:createHash('sha256').update(bytes).digest('hex'),service_root:root,chunk_messages:maxMessages,chunk_words:maxWords,samples:samples.length,requests:0,committed:0,failures:[],elapsed_ms:0};
const extractor=new Extractor(configFromEnv({MEMORY_MODE:'offline'}),{});
const started=Date.now();
try{
 for(const [index,sample] of samples.entries()){
  const tenant=String(index),store=new TenantStore(dir,tenant);
  try{
   for(const session of sample.sessions)for(const [part,messages] of chunks(session.messages,maxMessages,maxWords).entries()){
    const req={request_id:`${session.session_id}:${part}`,user_id:tenant,session_id:session.session_id,messages:structuredClone(messages)};
    result.requests++;
    try{
     const prepared=await extractor.prepare(req,store.snapshot(req.session_id),AbortSignal.timeout(5000));
     store.commit(req,hash(JSON.stringify(req)),prepared,store.revision());result.committed++;
    }catch(error){result.failures.push({sample:sample.sample_id,session:session.session_id,part,code:error.code??error.name,message:error.message});}
   }
  }finally{store.close();}
  if((index+1)%10===0)console.log(JSON.stringify({completed_samples:index+1,requests:result.requests,failures:result.failures.length}));
 }
 result.elapsed_ms=Date.now()-started;writeFileSync(output,JSON.stringify(result,null,2)+'\n',{flag:'wx'});
 console.log(JSON.stringify({output,samples:result.samples,requests:result.requests,committed:result.committed,failures:result.failures.length,elapsed_ms:result.elapsed_ms}));
}finally{rmSync(dir,{recursive:true,force:true});}
