import {readFileSync,writeFileSync,mkdirSync,readdirSync} from 'node:fs';
import {join,resolve} from 'node:path';
import {parseEnv} from 'node:util';
import {createHash} from 'node:crypto';
import {buildServer} from '../../service/dist/server.js';
import {configFromEnv} from '../../service/dist/config.js';
import {completion} from '../../eval/dist/models.js';
import {ANSWER_PROMPT} from '../../eval/dist/runner.js';
import {checkTemporalAnswer} from './temporal-checks.mjs';

const sha=s=>createHash('sha256').update(s).digest('hex');
const env={...parseEnv(readFileSync('.env','utf8')),MEMORY_EMBEDDING_DIGEST:'0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f'};
const runDir=resolve('artifacts/round2-temporal-live/'+new Date().toISOString().replace(/[:.]/g,'-'));mkdirSync(runDir,{recursive:true});
process.env.MEMORY_MODEL_AUDIT=join(runDir,'model-usage.jsonl');process.env.MEMORY_RETRIEVAL_AUDIT=join(runDir,'stages.jsonl');
const config={...configFromEnv(env),dataDir:join(runDir,'data'),port:0};const app=await buildServer(config);const base=await app.listen({host:'127.0.0.1',port:0});
const sourceFiles=readdirSync('service/src',{recursive:true}).filter(p=>p.endsWith('.ts')).map(p=>'service/src/'+p);
const report={acceptance_protocol:'temporal-synthetic-v3',answer_repetitions:3,check_sha256:sha(readFileSync('scripts/probes/temporal-checks.mjs')),probe_sha256:sha(readFileSync('scripts/probes/temporal-live.mjs')),run_dir:runDir,started_at:new Date().toISOString(),scope:'Five synthetic acceptance cases with real HTTP, remote extraction/rerank, local embeddings and the unchanged ordinary Answer prompt; not benchmark accuracy',answer_model:config.llmModel,answer_prompt_sha256:sha(ANSWER_PROMPT),source_sha256:Object.fromEntries(sourceFiles.map(p=>[p,sha(readFileSync(p))])),cases:[]};
const cases=[
 {id:'leap-day',messages:[['[Session time: 1 March 2024] I visited the Harbor Museum yesterday.','2024-03-01T00:00:00Z']],query:'What calendar date did I visit the Harbor Museum?'},
 {id:'month-precision',messages:[['I joined the night photography club in November 2024. I remember the month but not the exact day.','2025-01-01T00:00:00Z']],query:'In what month and year did I join the night photography club?'},
 {id:'trajectory',messages:[['I live in Oslo.','2026-01-01T00:00:00Z'],['I now live in Bergen.','2026-02-01T00:00:00Z'],['I now live in Tromso.','2026-03-01T00:00:00Z']],query:'How did my city change over our conversation, in order?'},
 {id:'forget-restore',messages:[['My access code is ZX-482. My manager is Clara.','2026-01-01T00:00:00Z'],['Forget my access code.','2026-02-01T00:00:00Z'],['Remember again: my access code is NEW-927.','2026-03-01T00:00:00Z']],query:'What is my access code now, and what did I ask you to do with the old one?'},
 {id:'synthetic-anchor',messages:[['[Session time: synthetic ordering marker 2000-01-01; not an event date] I saw a meteor shower yesterday.','2000-01-01T00:00:00Z']],query:'What exact calendar date did I see the meteor shower?'}
];
try{
 for(const c of cases){
  const row={id:c.id,query:c.query,adds:[],status:'running'};report.cases.push(row);
  try{
   for(const [index,[content,timestamp]] of c.messages.entries()){
    const body={request_id:c.id+'-'+index,user_id:'temporal-live:'+c.id,session_id:'s',messages:[{role:'user',content,timestamp}]};
    const started=performance.now();const response=await fetch(base+'/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(120000)});
    const receipt=await response.json();row.adds.push({status:response.status,elapsed_ms:performance.now()-started});if(!response.ok)throw Error('Add failed: '+JSON.stringify(receipt));
    console.log(JSON.stringify({event:'add_complete',case:c.id,index,status:response.status}));
   }
   const response=await fetch(base+'/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:'temporal-live:'+c.id,query:c.query,top_k:32}),signal:AbortSignal.timeout(60000)});
   if(!response.ok)throw Error('Search failed: '+response.status);row.memories=(await response.json()).data;
   row.answers=[];
   for(let repeat=0;repeat<report.answer_repetitions;repeat++){
    const answer=await completion(config.llmBase,config.llmKey,config.llmModel,[{role:'system',content:ANSWER_PROMPT},{role:'user',content:JSON.stringify({question:c.query,memories:row.memories})}]);
    row.answers.push({repeat,answer,answer_check:checkTemporalAnswer(c.id,answer)});
   }
   row.evidence_check=c.id!=='forget-restore'||!JSON.stringify(row.memories).includes('ZX-482');
   row.answer_passes=row.answers.filter(a=>a.answer_check).length;
   row.status=row.answer_passes===report.answer_repetitions&&row.evidence_check?'passed':'failed';
  }catch(error){row.status='error';row.error=error instanceof Error?error.message:'Unknown failure';}
  writeFileSync(join(runDir,'results.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({event:'case_complete',id:row.id,status:row.status}));
 }
 report.finished_at=new Date().toISOString();report.passed=report.cases.filter(c=>c.status==='passed').length;report.planned=cases.length;
 report.answer_passes=report.cases.reduce((n,c)=>n+(c.answer_passes??0),0);report.planned_answers=cases.length*report.answer_repetitions;
 report.status=report.passed===report.planned?'passed':'incomplete';writeFileSync(join(runDir,'results.json'),JSON.stringify(report,null,2)+'\n');writeFileSync('reports/round2-temporal-live.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({event:'complete',passed:report.passed,planned:report.planned,run_dir:runDir}));
}finally{await app.close();}
