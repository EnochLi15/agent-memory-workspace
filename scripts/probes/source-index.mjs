import assert from 'node:assert/strict';
import {mkdtempSync,rmSync,writeFileSync,readFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {Extractor,hash} from '../../service/dist/extraction.js';
import {Models} from '../../service/dist/models.js';
import {TenantStore} from '../../service/dist/storage.js';
import {collectCandidates,retrieve} from '../../service/dist/retrieval.js';
import {configFromEnv} from '../../service/dist/config.js';

const dir=mkdtempSync(join(tmpdir(),'source-index-real-'));
const config=configFromEnv({MEMORY_MODE:'enhanced',MEMORY_EMBEDDING_DIGEST:'0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f'});
const models=new Models(config);let store=new TenantStore(dir,'probe');const started=performance.now();
const message=content=>({role:'user',content,timestamp:'2026-01-01T00:00:00Z'});
const request=(id,messages)=>({request_id:id,user_id:'probe',session_id:'s',messages});
const report={checked_at:new Date().toISOString(),scope:'Real local Ollama embeddings and SQLite persistence; extraction fixture is deterministic, no remote LLM or benchmark accuracy claim',model:config.embeddingModel,digest:config.embeddingDigest,checks:{}};
try{
 const messages=[message('[Source id: fixture-1] I carried a cerulean umbrella through the monsoon.'),message('[Source id: fixture-2] My access code is ZX-482, and my violin is carved from maple.')];
 const extraction=new Extractor(config,{json:async()=>({facts:[{content:'My access code is ZX-482.',subject:'user',predicate:'access_code',value:'ZX-482',sources:[{index:1,quote:'My access code is ZX-482'}]}],operations:[]}),embedBatch:models.embedBatch.bind(models)});
 const req=request('initial',messages);const p=await extraction.prepare(req,store.snapshot('s'),AbortSignal.timeout(30000));
 assert.ok(p.passages.length>=2&&p.passages.every(p=>p.vector?.length===768));report.checks.source_vectors_768=true;
 store.commit(req,hash(JSON.stringify(req)),p,0);
 const q={user_id:'probe',query:'rain protection color',top_k:10};const vector=(await models.embedBatch([q.query],'search',AbortSignal.timeout(25000)))[0];
 const before=collectCandidates(store,q,vector,config);assert.ok(before.ranked.some(c=>c.signals.includes('source-semantic')&&c.fact.content.includes('cerulean umbrella')));report.checks.semantic_source_recall=true;
 const deletion=request('forget',[message('Forget my access code.')]);const offline=new Extractor({...config,mode:'offline'},{});
 const prepared=await offline.prepare(deletion,store.snapshot('s'),AbortSignal.timeout(1000));store.commit(deletion,hash(JSON.stringify(deletion)),prepared,store.revision());
 assert.doesNotMatch(JSON.stringify(store.passages()),/ZX-482/);assert.equal(store.lexicalPassages('ZX-482',20).length,0);report.checks.erased_source_and_fts=true;
 const remaining=store.passages().find(p=>p.content.includes('violin'));assert.ok(remaining&&remaining.vector===null&&remaining.content.includes('maple'));report.checks.retained_fragment_invalidates_old_vector=true;
 store.close();store=new TenantStore(dir,'probe');
 const rows=retrieve(store,{user_id:'probe',query:'violin maple',top_k:10},null,config).data;
 assert.match(JSON.stringify(rows),/violin is carved from maple/);assert.doesNotMatch(JSON.stringify(rows),/ZX-482/);report.checks.retained_detail_after_restart=true;
 assert.match(JSON.stringify(rows),/fixture-2/);report.checks.source_identifier_after_redaction=true;
 report.elapsed_ms=performance.now()-started;report.status='passed';
 report.source_sha256=Object.fromEntries(['service/src/passages.ts','service/src/extraction.ts','service/src/storage.ts','service/src/retrieval.ts','scripts/probes/source-index.mjs'].map(p=>[p,createHash('sha256').update(readFileSync(p)).digest('hex')]));
 writeFileSync('reports/round2-source-index-local-embedding.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
}finally{store.close();rmSync(dir,{recursive:true,force:true});}
