// Exact failed requests against copied snapshots. Prepare-only: never relabel
// the source format or commit across representation versions.
import {readFileSync,writeFileSync,mkdtempSync,mkdirSync,copyFileSync,readdirSync} from 'node:fs';
import {resolve,join} from 'node:path';import {parseEnv} from 'node:util';import {createHash} from 'node:crypto';
import {configFromEnv} from '../../service/dist/config.js';import {Models} from '../../service/dist/models.js';
import {Extractor} from '../../service/dist/extraction.js';import {TenantStore} from '../../service/dist/storage.js';
const sha=x=>createHash('sha256').update(x).digest('hex'),dir=mkdtempSync(resolve('artifacts/round2-source-routing-replay-'));
const specPath=process.argv.find(x=>x.startsWith('--spec='))?.slice(7)??'configs/round2-source-routing.json',spec=JSON.parse(readFileSync(specPath));
const snapshotOverride=process.argv.find(x=>x.startsWith('--snapshot='))?.slice(11);
const selected=process.argv.find(x=>x.startsWith('--case='))?.slice(7)??'all';
if(!['all','E10','B05'].includes(selected))throw Error('Expected --case=all|E10|B05');
const connection=process.argv.find(x=>x.startsWith('--connection='))?.slice(13)??'default';
if(!['default','close'].includes(connection))throw Error('Expected --connection=default|close');
const originalFetch=globalThis.fetch;
globalThis.fetch=async(input,init)=>{
 const remote=(typeof input==='string'?input:input instanceof URL?input.href:input.url).startsWith(config.llmBase);
 if(!remote)return originalFetch(input,init);
 const headers=new Headers(init?.headers);if(connection==='close')headers.set('Connection','close');
 try{return await originalFetch(input,{...init,headers});}
 catch(e){const chain=[e,e?.cause,e?.cause?.cause],socket=chain.find(x=>x?.socket)?.socket;
  const metrics=socket?Object.fromEntries(['bytesWritten','bytesRead','localPort'].filter(k=>typeof socket[k]==='number').map(k=>[k,socket[k]])):null;
  writeFileSync(join(dir,'transport-failures.jsonl'),JSON.stringify({at:new Date().toISOString(),connection,request_body_bytes:typeof init?.body==='string'?Buffer.byteLength(init.body):null,socket:metrics})+'\n',{flag:'a',mode:0o600});throw e;
 }
};
const config=configFromEnv({...parseEnv(readFileSync('.env','utf8')),...spec.defaults});
process.env.MEMORY_MODEL_TRACE=join(dir,'private-model-trace.jsonl');process.env.MEMORY_MODEL_AUDIT=join(dir,'model-calls.jsonl');
mkdirSync(join(dir,'source-snapshot'));const files=readdirSync('service/src').filter(f=>f.endsWith('.ts'));for(const f of files)copyFileSync('service/src/'+f,join(dir,'source-snapshot',f));copyFileSync(import.meta.filename,join(dir,'probe-source.mjs'));copyFileSync(specPath,join(dir,'spec.json'));
const report={protocol:'source-routing-replay-v3',selected_cases:selected,snapshot_override:snapshotOverride??null,spec_path:specPath,extraction_format:config.extractionFormat,llm_connection:connection,run_dir:dir,scope:'Real model prepare-only replay of selected exposed failed requests against exact copied snapshots. No migration, commit, full HTTP ingestion, benchmark accuracy or independent full repeat.',source_sha256:Object.fromEntries(files.map(f=>[f,sha(readFileSync('service/src/'+f))])),spec_sha256:sha(readFileSync(specPath)),started_at:new Date().toISOString(),status:'running',cases:[]};
const save=()=>writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');save();console.log(JSON.stringify({run_dir:dir,status:'running'}));
for(const parent of snapshotOverride?[snapshotOverride]:['artifacts/round2-E10-v8-failure-8nr_ipub','artifacts/round2-B05-v8-failure-6arzpegx'].filter(p=>selected==='all'||p.includes('round2-'+selected+'-'))){
 const request=JSON.parse(readFileSync(join(parent,'request.json'))),data=join(dir,'data'),folder=join(data,sha(request.user_id));mkdirSync(folder,{recursive:true});copyFileSync(join(parent,'memory.sqlite'),join(folder,'memory.sqlite'));
 const store=new TenantStore(data,request.user_id),before=store.snapshot(request.session_id),entry={parent:resolve(parent),request_sha256:sha(readFileSync(join(parent,'request.json'))),database_sha256:sha(readFileSync(join(parent,'memory.sqlite'))),revision:before.revision,source_format:store.meta('source_format'),status:'running'};report.cases.push(entry);save();const start=performance.now();
 try{const p=await new Extractor(config,new Models(config)).prepare(request,before,AbortSignal.timeout(config.addTimeout));entry.status='prepared';entry.prepared_format=p.sourceFormat;entry.route_rows=p.sourceOperationPlan?.route_review?.rows.map(r=>({instruction:r.instruction,route:r.route}));entry.used_history_batches=!!p.sourceOperationPlan?.batch_review;entry.fact_count=p.facts.length;entry.operation_count=p.operations.length;entry.degraded=p.degraded;writeFileSync(join(dir,request.user_id.includes('B05')?'B05-prepared.json':'E10-prepared.json'),JSON.stringify(p)+'\n');}
 catch(e){entry.status='failed';entry.error={code:e.code??null,message:e.message};}
 finally{entry.prepare_ms=performance.now()-start;entry.database_unchanged=sha(JSON.stringify(before))===sha(JSON.stringify(store.snapshot(request.session_id)));entry.source_format_unchanged=store.meta('source_format')===entry.source_format;store.close();save();console.log(JSON.stringify(entry));}
}
report.status=report.cases.every(c=>c.status==='prepared')?'all_prepared':'failures_retained';report.finished_at=new Date().toISOString();save();writeFileSync('reports/'+dir.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({status:report.status,run_dir:dir}));
