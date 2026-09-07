// Commit a prepared diagnostic into a private SQLite backup, never the live tenant.
import {readFileSync,writeFileSync,mkdtempSync,mkdirSync} from 'node:fs';import {resolve,join} from 'node:path';import {createHash} from 'node:crypto';import {createRequire} from 'node:module';import assert from 'node:assert/strict';
import {TenantStore} from '../../service/dist/storage.js';
const require=createRequire(new URL('../../service/package.json',import.meta.url)),Database=require('better-sqlite3'),sha=x=>createHash('sha256').update(x).digest('hex'),read=p=>JSON.parse(readFileSync(p,'utf8'));
const snapshotPath=resolve(process.argv[2]),preparedDir=resolve(process.argv[3]),saved=read(snapshotPath),preparation=read(join(preparedDir,'report.json')),prepared=read(join(preparedDir,'prepared.json'));
assert.equal(saved.provenance.sample,'F07_trajectory_ops','This review covers the teammate-disclosure case');assert.equal(preparation.status,'prepared');assert.equal(preparation.captured_snapshot_sha256,sha(readFileSync(snapshotPath)));assert.equal(preparation.prepared_sha256,sha(readFileSync(join(preparedDir,'prepared.json'))));
for(const [name,hash] of Object.entries(preparation.source_sha256))assert.equal(sha(readFileSync('service/src/'+name)),hash,'Prepared runtime source changed');
const manifest=read(join(saved.provenance.parent_run,'manifest.json'));assert.equal(manifest.service_config_sha256,saved.provenance.service_config_sha256);assert.equal(manifest.service_commit,saved.provenance.source_commit);
const dir=mkdtempSync(resolve('artifacts/round2-checkpoint-commit-')),tenantHash=sha(saved.request.user_id),destination=join(dir,'data',tenantHash);mkdirSync(destination,{recursive:true});
const original=new Database(join(manifest.service_configuration.dataDir,tenantHash,'memory.sqlite'),{readonly:true,fileMustExist:true});
try{await original.backup(join(destination,'memory.sqlite'));}finally{original.close();}
const store=new TenantStore(join(dir,'data'),saved.request.user_id);let report;
try{
 assert.deepEqual(store.snapshot(saved.request.session_id),saved.snapshot);
 const before=store.facts(),receipt=store.commit(saved.request,sha(JSON.stringify(saved.request)),prepared,saved.snapshot.revision),after=store.facts();
 const semantic=f=>Object.fromEntries(['id','content','subject','predicate','value','scope','kind','modality','cardinality','state'].map(k=>[k,f[k]]));
 const ownTherapy=before.filter(f=>f.subject==='user'&&/therapy|counsel|huang|sleep|session.frequency/i.test(f.predicate+' '+f.scope+' '+f.content));
 const unchanged=ownTherapy.every(f=>JSON.stringify(semantic(f))===JSON.stringify(semantic(after.find(a=>a.id===f.id))));
 const allOldUnchanged=before.every(f=>JSON.stringify(semantic(f))===JSON.stringify(semantic(after.find(a=>a.id===f.id))));
 const ownAfter=after.filter(f=>f.subject==='user'&&/therapy|counsel|huang|sleep|session.frequency/i.test(f.predicate+' '+f.scope+' '+f.content));
 const state={revision:store.revision(),facts:after,events:store.events(),passages:store.passages()};writeFileSync(join(dir,'state.json'),JSON.stringify(state)+'\n');
 report={protocol:'cloned-checkpoint-commit-v1',run_dir:dir,sample:saved.provenance.sample,snapshot_sha256:sha(readFileSync(snapshotPath)),prepared_sha256:preparation.prepared_sha256,original_revision:saved.snapshot.revision,committed_revision:store.revision(),receipt,original_own_therapy_fact_count:ownTherapy.length,own_therapy_facts_unchanged:unchanged,own_therapy_facts_after:ownAfter.length,all_prior_fact_count:before.length,all_prior_facts_unchanged:allOldUnchanged,review_correction:'The initial probe incorrectly assumed own therapy facts existed. The captured snapshot contains none; verify no assistant-only own therapy claim is invented, and all prior facts remain unchanged.',initial_probe_log_sha256:sha(readFileSync('artifacts/round2-F07-cloned-commit.log')),new_facts:after.filter(f=>!before.some(b=>b.id===f.id)).map(f=>({subject:f.subject,predicate:f.predicate,value:f.value,modality:f.modality,state:f.state})),state_sha256:sha(readFileSync(join(dir,'state.json'))),scope:'Commit on a read-only SQLite backup copied to a new private directory. Original campaign data and its failed score remain unchanged. Own-therapy preservation is a case-specific check, not broad lifecycle certification.'};
 assert.equal(ownTherapy.length,0);assert.equal(ownAfter.length,0);assert.ok(allOldUnchanged);assert.ok(unchanged);assert.equal(store.revision(),saved.snapshot.revision+1);
}finally{store.close();}
writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');writeFileSync('reports/round2-checkpoint-commit-'+dir.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({run_dir:dir,sample:report.sample,committed_revision:report.committed_revision,own_therapy_fact_count:report.original_own_therapy_fact_count,own_therapy_facts_unchanged:report.own_therapy_facts_unchanged}));
