import {readFileSync,appendFileSync,writeFileSync} from 'node:fs';
import {dirname,join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';

const read=p=>JSON.parse(readFileSync(p,'utf8'));
const plan=read(process.argv[2]),campaign=dirname(process.argv[2]);
const {Models}=await import(pathToFileURL(join(plan.candidate_dist,'models.js')).href);
const {QueryFocusClassifier}=await import(pathToFileURL(join(plan.candidate_dist,'query-focus.js')).href);
const config={...read(plan.config),llmKey:process.env.MEMORY_LLM_API_KEY};
const classifier=new QueryFocusClassifier(config,new Models(config));
const abort=new AbortController();
for(const signal of ['SIGTERM','SIGINT'])process.once(signal,()=>abort.abort(new Error('Owned validation interrupted')));
const rows=read(plan.queries),results=[];
for(const row of rows){
  abort.signal.throwIfAborted();
  if(createHash('sha256').update(row.query).digest('hex')!==row.query_sha256)throw Error('Query changed');
  const start=performance.now();
  const result=await classifier.classify(row.query,AbortSignal.any([abort.signal,AbortSignal.timeout(10000)]),
    {user_id:'query-focus-validation',request_id:plan.campaign+':'+row.qid});
  const recorded={qid:row.qid,query_sha256:row.query_sha256,...result,elapsed_ms:performance.now()-start};
  appendFileSync(join(campaign,'results.private.jsonl'),JSON.stringify(recorded)+'\n',{mode:0o600});
  results.push(recorded);
  process.stdout.write(JSON.stringify({qid:row.qid,focus:result.focus,outcome:result.outcome,elapsed_ms:recorded.elapsed_ms})+'\n');
}
classifier.clear();
writeFileSync(join(campaign,'focus-results.private.json'),JSON.stringify(results,null,2)+'\n',{flag:'wx',mode:0o600});
