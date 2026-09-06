// Generic transaction diagnostic: source DB is read-only; only a private backup is committed.
import {readFileSync,writeFileSync,mkdtempSync,mkdirSync,copyFileSync} from 'node:fs';import {resolve,join} from 'node:path';import {createHash} from 'node:crypto';import {createRequire} from 'node:module';import assert from 'node:assert/strict';
import {TenantStore} from '../../service/dist/storage.js';import {retrieve} from '../../service/dist/retrieval.js';import {configFromEnv} from '../../service/dist/config.js';
const require=createRequire(new URL('../../service/package.json',import.meta.url)),Database=require('better-sqlite3'),sha=x=>createHash('sha256').update(x).digest('hex'),read=p=>JSON.parse(readFileSync(p,'utf8'));
const snapshotPath=resolve(process.argv[2]),preparedDir=resolve(process.argv[3]),saved=read(snapshotPath),prep=read(join(preparedDir,'report.json'));
assert.equal(saved.protocol,'failed-development-chunk-snapshot-v1');assert.equal(prep.status,'prepared');assert.equal(prep.captured_snapshot_sha256,sha(readFileSync(snapshotPath)));assert.equal(prep.prepared_sha256,sha(readFileSync(join(preparedDir,'prepared.json'))));
for(const [name,digest] of Object.entries(prep.source_sha256)){assert.ok(!name.includes('/')&&!name.includes('..'));assert.equal(sha(readFileSync('service/src/'+name)),digest,'Runtime must match preparation');}
const manifest=read(join(saved.provenance.parent_run,'manifest.json'));assert.equal(manifest.service_config_sha256,saved.provenance.service_config_sha256);assert.equal(manifest.service_commit,saved.provenance.source_commit);
const dir=mkdtempSync(resolve('artifacts/round2-checkpoint-transaction-')),tenantHash=sha(saved.request.user_id),destination=join(dir,'data',tenantHash);mkdirSync(destination,{recursive:true});copyFileSync(import.meta.filename,join(dir,'probe-source.mjs'));
const original=new Database(join(manifest.service_configuration.dataDir,tenantHash,'memory.sqlite'),{readonly:true,fileMustExist:true});
try{await original.backup(join(destination,'memory.sqlite'));}finally{original.close();}
const store=new TenantStore(join(dir,'data'),saved.request.user_id),report={protocol:'cloned-checkpoint-transaction-v1',run_dir:dir,sample:saved.provenance.sample,snapshot_sha256:sha(readFileSync(snapshotPath)),preparation_report_sha256:sha(readFileSync(join(preparedDir,'report.json'))),prepared_sha256:prep.prepared_sha256,source_sha256:prep.source_sha256,probe_sha256:sha(readFileSync(import.meta.filename)),original_revision:saved.snapshot.revision,status:'running',preparation_scope:prep.scope,scope:'One exact saved failed chunk with preparation provenance recorded separately, committed only on a private backup. Deterministic lexical queries and stored state are diagnostics, not a fresh complete sample, an Answer/Judge run or a replacement for the original failed benchmark.'};
try{
 assert.deepEqual(store.snapshot(saved.request.session_id),saved.snapshot);
 const before=store.facts();
 try{report.receipt=store.commit(saved.request,sha(JSON.stringify(saved.request)),read(join(preparedDir,'prepared.json')),saved.snapshot.revision);report.status='committed';}
 catch(e){report.status='failed';report.error={code:e.code??null,message:e.message};}
 const state={revision:store.revision(),facts:store.facts(),events:store.events(),passages:store.passages(),raw:store.raw()};writeFileSync(join(dir,'state.json'),JSON.stringify(state)+'\n');report.committed_revision=store.revision();report.state_sha256=sha(readFileSync(join(dir,'state.json')));
 const semantic=f=>Object.fromEntries(['content','subject','predicate','value','scope','kind','modality','cardinality','state'].map(k=>[k,f?.[k]]));
 report.changed_prior_facts=before.flatMap(b=>{const a=state.facts.find(f=>f.id===b.id);return JSON.stringify(semantic(a))===JSON.stringify(semantic(b))?[]:[{id:b.id,before:semantic(b),after:semantic(a)}];});
 report.new_fact_count=state.facts.filter(f=>!before.some(b=>b.id===f.id)).length;
 const config=configFromEnv({MEMORY_MODE:'offline',MEMORY_RAW_FALLBACK:'true',MEMORY_EVENT_VIEW:'true'});
 const queries=process.argv.slice(4).map(query=>({query,memories:retrieve(store,{user_id:saved.request.user_id,query,top_k:100},null,config).data}));
 writeFileSync(join(dir,'lexical-queries.json'),JSON.stringify(queries)+'\n');report.lexical_queries_sha256=sha(readFileSync(join(dir,'lexical-queries.json')));
}finally{store.close();}
writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');writeFileSync('reports/round2-checkpoint-transaction-'+dir.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({run_dir:dir,sample:report.sample,status:report.status,original_revision:report.original_revision,committed_revision:report.committed_revision,changed_prior_count:report.changed_prior_facts?.length,error:report.error}));
