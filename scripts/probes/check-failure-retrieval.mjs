// Controlled posthoc queries for already exposed failed-prefix replays.
// This diagnostic never sends references/rubrics to the memory service or Answer.
import {readFileSync,writeFileSync,mkdirSync,appendFileSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {parseEnv} from 'node:util';
import {createHash} from 'node:crypto';
import {buildServer} from '../../service/dist/server.js';
import {configFromEnv} from '../../service/dist/config.js';
import {completion} from '../../eval/dist/models.js';
import {ANSWER_PROMPT} from '../../eval/dist/runner.js';
const dir=resolve(process.argv[2]),sample=process.argv[3];
const questions={
 D24_reflect:['Why did I choose the Outback over the RAV4?','What plans did I mention for researching investments and consulting a financial advisor?'],
 D17_remember:['What are my default browsers on my work laptop and iPhone, and what browser does Kevin use?','What information do you still have about my old tablet and its bookmark syncing issue?'],
 E03_reflect:['What did I say about Martin and Rebecca, the handoff, and the group context?'],
 C30_update:['What are my current automatic investment amount, schedule and fund, and what changed from my previous setup?'],
};
if(!questions[sample])throw Error('Unsupported exposed case');
const sha=x=>createHash('sha256').update(x).digest('hex');
const manifest=JSON.parse(readFileSync(join(dir,'results.json')));
if(!manifest.cases.some(c=>c.user_id.endsWith(':'+sample)&&c.status==='prefix_accepted'))throw Error('Prefix did not finish successfully');
const sourceDifferences=[];
for(const [path,hash] of Object.entries(manifest.source_sha256)){const actual=sha(readFileSync(path));if(actual!==hash){if(path!=='service/src/prompts.ts')throw Error('Service source changed since ingestion: '+path);sourceDifferences.push({path,ingest_sha256:hash,query_sha256:actual,reason:'Only the extraction prompt changed; this read-only probe never invokes extraction.'});}}
const output=join(dir,'retrieval-probe-v1');mkdirSync(output);
const e=parseEnv(readFileSync('.env','utf8'));
const config={...configFromEnv({...e,MEMORY_EMBEDDING_DIGEST:'0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f'}),llmModel:manifest.extraction_model,dataDir:join(dir,'data'),port:0};
process.env.MEMORY_MODEL_AUDIT=join(output,'retrieval-model-usage.jsonl');
const app=await buildServer(config),base=await app.listen({host:'127.0.0.1',port:0});
const report={protocol:'exposed-failed-prefix-retrieval-v1',parent_results_sha256:sha(readFileSync(join(dir,'results.json'))),sample,source_differences:sourceDifferences,answer_model:'gpt-5.4-mini',answer_prompt_sha256:sha(ANSWER_PROMPT),retrieval_model:config.llmModel,questions:[],scope:'Posthoc diagnostic queries, not benchmark scores. Inspect stored answers before assigning semantic pass.'};
try{
 for(const query of questions[sample]){
  const start=performance.now();const r=await fetch(base+'/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query,user_id:'round2-prefix:'+sample,top_k:100}),signal:AbortSignal.timeout(60000)});
  const body=await r.json();if(!r.ok)throw Error('Search HTTP '+r.status+' '+JSON.stringify(body));
  const answer=await completion(e.MEMORY_LLM_BASE_URL,e.MEMORY_LLM_API_KEY,report.answer_model,[{role:'system',content:ANSWER_PROMPT},{role:'user',content:JSON.stringify({question:query,memories:body.data})}]);
  appendFileSync(join(output,'answers.jsonl'),JSON.stringify({query,memories:body.data,answer})+'\n');
  report.questions.push({query,evidence_count:body.data.length,elapsed_ms:performance.now()-start});console.log(JSON.stringify({completed:report.questions.length,planned:questions[sample].length}));
 }
}finally{await app.close();}
report.answers_sha256=sha(readFileSync(join(output,'answers.jsonl')));writeFileSync(join(output,'report.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({complete:true,output}));
