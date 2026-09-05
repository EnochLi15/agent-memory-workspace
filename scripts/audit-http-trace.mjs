// Audit saved service-bound payloads against the exact evaluated dataset.
// Does not call the service, Answer, or Judge, and never writes private answers.
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {resolve,join,dirname} from 'node:path';
import {createHash} from 'node:crypto';
import {chunks} from '../eval/dist/adapters.js';
const args=process.argv.slice(2);
const get=k=>args.includes(k)?args[args.indexOf(k)+1]:undefined;
const runId=get('--run-id'),dataFile=get('--data'),output=get('--output');
if(!runId||!dataFile||!output||!/^[A-Za-z0-9_.-]+$/.test(runId))throw Error('Use --run-id ID --data PATH --output PATH');
const root=resolve(dirname(new URL(import.meta.url).pathname),'..');
const directory=join(root,'eval/artifacts',runId);
const sha=x=>createHash('sha256').update(x).digest('hex');
const canonical=x=>JSON.stringify(x,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b))):v);
const manifestBytes=readFileSync(join(directory,'manifest.json'));
const manifest=JSON.parse(manifestBytes);
if(manifest.status!=='finished')throw Error('Only finished traces can receive a final audit');
const dataBytes=readFileSync(dataFile);
if(sha(dataBytes)!==manifest.dataset_sha256)throw Error('Dataset hash mismatch');
const data=JSON.parse(dataBytes),expectedAdds=new Map(),expectedSearches=new Map();
let questionCount=0;
for(const sampleId of manifest.samples){
 const sample=data.find(s=>s.sample_id===sampleId);
 if(!sample)throw Error('Manifest sample absent from dataset');
 const userId=`${manifest.memory_namespace}:${sample.benchmark}:${sample.sample_id}`;
 for(const session of sample.sessions){
  const messages=session.messages.map(({role,content,timestamp})=>({role,content,timestamp}));
  for(const [i,part] of chunks(messages,manifest.chunk_messages,manifest.chunk_words).entries()){
   const request_id=`${userId}:${session.session_id}:${i}`;
   expectedAdds.set(request_id,sha(canonical({request_id,user_id:userId,session_id:session.session_id,messages:part})));
  }
 }
 for(const q of sample.questions){
  const body={query:q.question,user_id:userId,top_k:manifest.top_k,...(q.options?{options:q.options}:{})};
  const key=sha(canonical(body));expectedSearches.set(key,(expectedSearches.get(key)??0)+1);questionCount++;
 }
}
const traceBytes=readFileSync(join(directory,'requests.jsonl'));
const rows=traceBytes.toString().split('\n').filter(Boolean).map(JSON.parse);
const uniqueAdds=new Set(),searchCounts=new Map(),violations=[];
let adds=0,searches=0;
for(const [index,row] of rows.entries()){
 const body=row.body;
 if(row.path==='/add'){
  adds++;
  if(!body||sha(canonical(body))!==expectedAdds.get(body.request_id))violations.push({line:index+1,reason:'add_payload_not_exact_whitelisted_dataset_chunk'});
  else uniqueAdds.add(body.request_id);
 }else if(row.path==='/search'){
  searches++;const key=sha(canonical(body));const count=(searchCounts.get(key)??0)+1;searchCounts.set(key,count);
  if(count>(expectedSearches.get(key)??0))violations.push({line:index+1,reason:'search_payload_or_count_not_in_whitelisted_questions'});
 }else violations.push({line:index+1,reason:'unexpected_service_path'});
}
const report={run_id:runId,checked_at:new Date().toISOString(),passed:violations.length===0,
 hashes:{manifest:sha(manifestBytes),dataset:sha(dataBytes),requests:sha(traceBytes),audit_script:sha(readFileSync(new URL(import.meta.url))),chunk_implementation:sha(readFileSync(join(root,'eval/dist/adapters.js')))},
 counts:{planned_questions:manifest.planned_questions,dataset_questions_in_selected_samples:questionCount,expected_add_chunks:expectedAdds.size,observed_add_attempts:adds,distinct_observed_adds:uniqueAdds.size,observed_searches:searches},violations,
 scope:'Exact saved HTTP payloads match role/content/timestamp-only conversation chunks or query/options/top_k-only questions. Repeated add IDs must have identical dataset payloads. Missing requests may reflect recorded service errors or resumed ingestion, not a successful-completion claim. Answer outbound traffic is not captured by this trace; its field whitelist is verified separately in source. No semantic claim that public conversation text lacks answer information.'};
mkdirSync(dirname(resolve(output)),{recursive:true});writeFileSync(output,JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify({run_id:runId,passed:report.passed,counts:report.counts,violations:violations.length}));
if(violations.length)process.exitCode=1;
