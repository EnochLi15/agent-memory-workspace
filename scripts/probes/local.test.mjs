import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,copyFileSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {localEnvironment,root} from '../local.mjs';

test('offline ignores inherited enhanced flags and isolates persistent storage',()=>{
 const env=localEnvironment('offline',root,{MEMORY_MODE:'enhanced',MEMORY_SOURCE_FIRST:'true',MEMORY_SOURCE_OPERATIONS:'true',MEMORY_DATA_DIR:'/data',MEMORY_LLM_API_KEY:'test-secret'});
 assert.equal(env.MEMORY_MODE,'offline');assert.equal(env.MEMORY_SOURCE_FIRST,'false');assert.equal(env.MEMORY_SOURCE_OPERATIONS,'false');
 assert.equal(env.MEMORY_LLM_API_KEY,'');assert.equal(env.MEMORY_DATA_DIR,join(root,'service/.data/local-offline'));
 assert.equal(env.HOST,'127.0.0.1');
});
test('connections prefer shell then local file; behavior stays pinned',()=>{
 const base=mkdtempSync(join(tmpdir(),'memory-local-'));
 try{
  mkdirSync(join(base,'configs'));copyFileSync(join(root,'configs/v1-bigmodel-enhanced.env'),join(base,'configs/v1-bigmodel-enhanced.env'));
  writeFileSync(join(base,'.env'),'MEMORY_LLM_BASE_URL=http://127.0.0.1:9000/v1\nMEMORY_LLM_API_KEY=old\nMEMORY_LLM_MODEL=wrong\n');
  writeFileSync(join(base,'.env.local'),'MEMORY_LLM_API_KEY="local # key"\nLOCAL_DATA_DIR=service/.data/custom\nLOCAL_PORT=8099\n');
  const env=localEnvironment('enhanced',base,{LOCAL_PORT:'8100',MEMORY_LLM_MODEL:'also-wrong'});
  assert.equal(env.MEMORY_LLM_API_KEY,'local # key');assert.equal(env.PORT,'8100');assert.equal(env.MEMORY_LLM_MODEL,'glm-5.2');assert.equal(env.MEMORY_EXTRACTION_WORKERS,'1');
  assert.equal(env.MEMORY_DATA_DIR,join(base,'service/.data/custom'));assert.equal(env.MEMORY_EMBEDDING_BASE_URL,'http://127.0.0.1:11434');
 }finally{rmSync(base,{recursive:true,force:true});}
});
test('invalid mode and port fail before spawning service',()=>{
 assert.throws(()=>localEnvironment('typo'),/LOCAL_MODE/);
 assert.throws(()=>localEnvironment('offline',root,{LOCAL_PORT:'8088oops'}),/LOCAL_PORT/);
});
