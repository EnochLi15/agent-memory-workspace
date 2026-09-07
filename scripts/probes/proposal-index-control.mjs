// Replay the three unchanged LoCoMo extraction packets. Default captures the
// new labeled verification input; --live runs all remaining model stages and
// commits only to an isolated database. No benchmark latency claim.
import {readFileSync,writeFileSync,mkdtempSync,mkdirSync,copyFileSync,readdirSync} from 'node:fs';
import {resolve,join} from 'node:path';import {pathToFileURL} from 'node:url';
import {parseEnv} from 'node:util';import {createHash} from 'node:crypto';
const arg=(name,fallback)=>process.argv.find(x=>x.startsWith('--'+name+'='))?.slice(name.length+3)??fallback;
const runtime=resolve(arg('runtime','artifacts/round2-service-shard-repair'));
const parent=resolve(arg('snapshot','artifacts/round2-LoCoMo-87d67b1-repair-failure-elj99ij1')),live=process.argv.includes('--live');
const {Extractor,hash}=await import(pathToFileURL(join(runtime,'dist/extraction.js')));
const {configFromEnv}=await import(pathToFileURL(join(runtime,'dist/config.js')));
const {Models}=await import(pathToFileURL(join(runtime,'dist/models.js')));
const {TenantStore}=await import(pathToFileURL(join(runtime,'dist/storage.js')));
const {ServiceError}=await import(pathToFileURL(join(runtime,'dist/types.js')));
const sha=x=>createHash('sha256').update(x).digest('hex'),dir=mkdtempSync(resolve('artifacts/round2-proposal-index-control-'));
const req=JSON.parse(readFileSync(parent+'/request.json')),spec=JSON.parse(readFileSync(parent+'/spec.json'));
const config={...configFromEnv({...parseEnv(readFileSync('.env','utf8')),...spec.defaults}),dataDir:join(dir,'data')};
const saved=readFileSync(parent+'/private-model-trace.jsonl','utf8').trim().split('\n').map(JSON.parse).filter(r=>r.outcome==='ok'&&r.purpose==='extraction'),used=new Set();
mkdirSync(join(config.dataDir,sha(req.user_id)),{recursive:true});copyFileSync(parent+'/memory.sqlite',join(config.dataDir,sha(req.user_id),'memory.sqlite'));
process.env.MEMORY_MODEL_TRACE=join(dir,'private-model-trace.jsonl');process.env.MEMORY_MODEL_AUDIT=join(dir,'model-calls.jsonl');
mkdirSync(join(dir,'source-snapshot'));const files=readdirSync(join(runtime,'src')).filter(f=>f.endsWith('.ts'));for(const f of files)copyFileSync(join(runtime,'src',f),join(dir,'source-snapshot',f));copyFileSync(import.meta.filename,join(dir,'probe-source.mjs'));
const report={protocol:'proposal-index-control-v1',run_dir:dir,runtime,parent,live,source_sha256:Object.fromEntries(files.map(f=>[f,sha(readFileSync(join(runtime,'src',f)))])),started_at:new Date().toISOString(),status:'running',stages:[],checks:[],scope:'Saved extraction packets replayed only on exact system/input match. A live run gives remaining stages a fresh budget and commits only to a private DB copy; it is not a fresh whole-request or benchmark latency result.'};
const save=()=>writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');save();console.log(JSON.stringify({run_dir:dir,status:'running',live}));
const models=new Models(config),real=models.json.bind(models);let verificationReached=false;
models.json=async(system,input,signal,context)=>{
 const index=saved.findIndex((r,i)=>!used.has(i)&&r.system===system&&r.input===input&&r.purpose===context.purpose);
 if(index>=0){used.add(index);report.stages.push({purpose:context.purpose,mode:'saved_exact_packet',input_sha256:sha(input)});return structuredClone(saved[index].output);}
 if(context.purpose==='extraction')throw Error('Unexpected or changed extraction packet');
 if(context.purpose==='verification'&&!verificationReached){
  const x=JSON.parse(input),old=savedVerification;
  report.proposal_counts={facts:x.PROPOSAL.facts.length,operations:x.PROPOSAL.operations.length};
  report.labels_complete=x.PROPOSAL.facts.every((f,i)=>f.fact_index===i)&&x.PROPOSAL.operations.every((o,i)=>o.operation_index===i);
  const stripped=structuredClone(x);for(const f of stripped.PROPOSAL.facts)delete f.fact_index;for(const o of stripped.PROPOSAL.operations)delete o.operation_index;
  report.verification_payload_unchanged_except_labels=JSON.stringify(stripped)===JSON.stringify(JSON.parse(old.input));
  report.observed_neighbor_labels=[62,63].map(index=>({index,label:x.PROPOSAL.facts[index]?.fact_index,predicate:x.PROPOSAL.facts[index]?.predicate,modality:x.PROPOSAL.facts[index]?.modality}));
  writeFileSync(join(dir,'private-verification-input.json'),JSON.stringify({system,input}),{mode:0o600});
  verificationReached=true;
  if(!report.labels_complete||!report.verification_payload_unchanged_except_labels)throw new ServiceError('EVIDENCE_VALIDATION','Unexpected proposal input change');
  if(!live)throw new ServiceError('EVIDENCE_VALIDATION','Control stopped at labeled verification');
 }
 if(!live)throw Error('Unexpected stage before labeled verification');
 report.stages.push({purpose:context.purpose,mode:'real',input_sha256:sha(input)});return real(system,input,signal,context);
};
const savedVerification=readFileSync(parent+'/private-model-trace.jsonl','utf8').trim().split('\n').map(JSON.parse).find(r=>r.outcome==='ok'&&r.purpose==='verification');
const originalHash=sha(readFileSync(parent+'/memory.sqlite')),store=new TenantStore(config.dataDir,req.user_id),before=store.snapshot(req.session_id);
try{
 const prepared=await new Extractor(config,models).prepare(req,before,AbortSignal.timeout(config.addTimeout));
 if(used.size!==saved.length)throw Error('Saved extraction packet coverage incomplete');
 writeFileSync(join(dir,'prepared.json'),JSON.stringify(prepared)+'\n',{mode:0o600});report.degraded=prepared.degraded;
 const receipt=store.commit(req,hash(JSON.stringify(req)),prepared,before.revision);
 report.checks=[{name:'single_revision',pass:store.revision()===before.revision+1},{name:'receipt_persisted',pass:!!store.receipt(req.request_id,hash(JSON.stringify(req)))},{name:'no_degradation',pass:prepared.degraded.length===0}];
 report.receipt=receipt;report.status=report.checks.every(c=>c.pass)?'committed_controls_passed':'committed_controls_failed';
}catch(error){report.status=!live&&verificationReached&&report.labels_complete&&report.verification_payload_unchanged_except_labels?'labeled_verification_entry_confirmed':'failed';report.error={code:error.code??null,message:error.message};report.database_unchanged=JSON.stringify(before)===JSON.stringify(store.snapshot(req.session_id));}
finally{store.close();report.saved_packets_used=used.size;report.saved_packets_planned=saved.length;report.original_database_unchanged=sha(readFileSync(parent+'/memory.sqlite'))===originalHash;report.finished_at=new Date().toISOString();save();writeFileSync('reports/'+dir.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({status:report.status,run_dir:dir,saved_packets_used:used.size,checks:report.checks,error:report.error}));}
