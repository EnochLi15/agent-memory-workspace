// One fresh HTTP /add against an isolated pre-failure database snapshot.
// No saved model responses, no replacement of the original failure or score.
import {readFileSync,writeFileSync,mkdirSync,mkdtempSync,copyFileSync,readdirSync} from 'node:fs';
import {join,resolve} from 'node:path';import {pathToFileURL} from 'node:url';import {parseEnv} from 'node:util';import {createHash} from 'node:crypto';
const arg=name=>process.argv.find(x=>x.startsWith('--'+name+'='))?.slice(name.length+3);
if(!arg('runtime')||!arg('snapshot'))throw Error('Provide --runtime=PATH --snapshot=PATH and optional --spec=PATH');
const runtime=resolve(arg('runtime')),parent=resolve(arg('snapshot')),specFile=resolve(arg('spec')??join(parent,'spec.json'));
const {buildServer}=await import(pathToFileURL(join(runtime,'dist/server.js'))),{configFromEnv}=await import(pathToFileURL(join(runtime,'dist/config.js'))),{TenantStore}=await import(pathToFileURL(join(runtime,'dist/storage.js'))),{addSchema}=await import(pathToFileURL(join(runtime,'dist/types.js')));
const sha=x=>createHash('sha256').update(x).digest('hex'),dir=mkdtempSync(resolve('artifacts/round2-fresh-failed-add-'));
const req=JSON.parse(readFileSync(join(parent,'request.json'),'utf8')),spec=JSON.parse(readFileSync(specFile,'utf8'));
const profile=arg('profile')??'sources',attempts=Number(arg('attempts')??1);
if(!Number.isInteger(attempts)||attempts<1||attempts>3)throw Error('Attempts must be between 1 and 3');
if(arg('profile')&&!spec.profiles?.[profile])throw Error('Unknown profile');
const config={...configFromEnv({...parseEnv(readFileSync('.env','utf8')),...spec.defaults,...spec.profiles?.[profile]?.environment}),port:0,dataDir:join(dir,'data')};
mkdirSync(join(config.dataDir,sha(req.user_id)),{recursive:true});copyFileSync(join(parent,'memory.sqlite'),join(config.dataDir,sha(req.user_id),'memory.sqlite'));
process.env.MEMORY_MODEL_TRACE=join(dir,'private-model-trace.jsonl');process.env.MEMORY_MODEL_AUDIT=join(dir,'model-calls.jsonl');
const {llmKey,...safeConfig}=config,sourceFiles=readdirSync(join(runtime,'src')).filter(f=>f.endsWith('.ts'));
const report={protocol:'fresh-failed-add-http-control-v1',run_dir:dir,runtime,parent,started_at:new Date().toISOString(),request_id:req.request_id,request_sha256:sha(JSON.stringify(req)),spec_sha256:sha(readFileSync(specFile)),service_configuration:safeConfig,source_sha256:Object.fromEntries(sourceFiles.map(f=>[f,sha(readFileSync(join(runtime,'src',f)))])),status:'running',checks:[],scope:'One fresh HTTP request, every model stage generated anew, against a copied pre-failure database. No saved LLM packets or fresh full-background ingestion. Shared gateway timings may be contended.'};
copyFileSync(import.meta.filename,join(dir,'probe-source.mjs'));const save=()=>{writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');writeFileSync('reports/'+dir.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');};save();console.log(JSON.stringify({run_dir:dir,request_id:req.request_id,status:'running'}));
const originalHash=sha(readFileSync(join(parent,'memory.sqlite'))),payloadHash=sha(JSON.stringify(addSchema.parse(req)));
let store=new TenantStore(config.dataDir,req.user_id);const before=store.snapshot(req.session_id);if(store.receipt(req.request_id,payloadHash))throw Error('Snapshot already has a receipt');store.close();
const app=await buildServer(config),base=await app.listen({host:'127.0.0.1',port:0});
try{
 report.attempts=[];report.max_attempts=attempts;
 for(let attempt=0;attempt<attempts;attempt++){
  const started=performance.now(),response=await fetch(base+'/add',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(req),signal:AbortSignal.timeout(config.addTimeout+1000)});
  report.http_status=response.status;report.http_elapsed_ms=performance.now()-started;report.response=await response.json();report.status=response.ok?'http_accepted':'http_rejected';
  report.attempts.push({attempt,http_status:response.status,elapsed_ms:report.http_elapsed_ms,request_sha256:report.request_sha256,code:report.response?.error?.code??null});save();
  if(response.status!==503||report.response?.error?.code!=='WRITE_CONTINUATION_PENDING')break;
 }
}catch(error){report.status='failed';report.error={code:error.code??null,message:error.message};}
finally{
 await app.close();store=new TenantStore(config.dataDir,req.user_id);
 try{
  const after=store.snapshot(req.session_id),receipt=store.receipt(req.request_id,payloadHash);
  report.checks=report.http_status===200?[{name:'single_revision',pass:after.revision===before.revision+1},{name:'receipt_persisted',pass:!!receipt}]:[{name:'failed_request_not_committed',pass:after.revision===before.revision&&!receipt},{name:'snapshot_unchanged',pass:JSON.stringify(after)===JSON.stringify(before)}];
  report.final_revision=after.revision;report.original_database_unchanged=sha(readFileSync(join(parent,'memory.sqlite')))===originalHash;
 }finally{store.close();}
 report.finished_at=new Date().toISOString();save();console.log(JSON.stringify({run_dir:dir,status:report.status,http_status:report.http_status,checks:report.checks,response:report.response,error:report.error}));
}
