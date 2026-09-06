// Assistant-authored controls for the new relation classifier; not a benchmark.
import {readFileSync,writeFileSync,mkdtempSync,readdirSync} from 'node:fs';import {resolve,join} from 'node:path';import {parseEnv} from 'node:util';import {createHash} from 'node:crypto';
import {Models} from '../../service/dist/models.js';import {configFromEnv} from '../../service/dist/config.js';
import {transitionWork,decodeTransitions,TRANSITION_PROMPT} from '../../service/dist/transitions.js';
const cases=[
 ['coffee_detail_routine','I drink black drip coffee every morning, no milk, no sugar.','I like the routine of just a plain cup of coffee.','black drip coffee','plain coffee routine','coffee_preference','compatible'],
 ['preference_reason','I prefer texting or online booking for appointments.','Phone calls drain me, so asynchronous communication is easier.','texting or online booking','asynchronous communication is easier','communication_preference','compatible'],
 ['current_browser_update','My default browser on my work laptop is Firefox.','I switched my default browser on my work laptop to Brave.','Firefox','Brave','default_browser','exclusive'],
 ['current_city_update','I live in Paris.','I moved to Berlin and now live there.','Paris','Berlin','current_city','exclusive'],
 ['coffee_change','I drink black drip coffee with no milk.','I now prefer a latte with whole milk instead of black coffee.','black drip coffee','latte with whole milk','coffee_preference','exclusive'],
 ['different_events','I visited Paris in January.','I visited Berlin in February.','Paris in January','Berlin in February','travel_event','compatible'],
];
const fact=(id,text,value,predicate,scope)=>({id,content:text,value,predicate,scope,subject:'user',kind:predicate==='travel_event'?'event':'fact',modality:'confirmed',cardinality:'single',time_text:'',valid_from:null,valid_to:null,supersedes:[],depends_on:[],source_ids:[id+'-source'],source_quotes:[text],created_at:'2026-01-01T00:00:00Z',observed_at:'2026-01-01T00:00:00Z',state:'active',vector:null,entities:[],revision:1});
const prior=cases.map((c,i)=>fact('p'+i,c[1],c[3],c[5],'context-'+i)),incoming=cases.map((c,i)=>fact('n'+i,c[2],c[4],c[5],'context-'+i));
const req={user_id:'controls',request_id:'controls',session_id:'controls',messages:[]};const work=transitionWork(req,prior,incoming,[]);
const input={CANDIDATES:work.candidates.map((c,index)=>({index,...c}))};
const dir=mkdtempSync(resolve('artifacts/round2-transition-controls-')),sha=x=>createHash('sha256').update(x).digest('hex');
const spec=JSON.parse(readFileSync('configs/round2-quality-transitions-v3.json')),config=configFromEnv({...parseEnv(readFileSync('.env','utf8')),...spec.defaults});process.env.MEMORY_MODEL_AUDIT=join(dir,'model-usage.jsonl');
writeFileSync(join(dir,'input.json'),JSON.stringify(input)+'\n');writeFileSync(join(dir,'labels.json'),JSON.stringify(cases)+'\n');writeFileSync(join(dir,'probe-source.mjs'),readFileSync(import.meta.filename));
const report={protocol:'state-transition-controls-v3',run_dir:dir,planned:cases.length,input_sha256:sha(JSON.stringify(input)),prompt_sha256:sha(TRANSITION_PROMPT),probe_sha256:sha(readFileSync(import.meta.filename)),source_sha256:Object.fromEntries(readdirSync('service/src').filter(p=>p.endsWith('.ts')).map(p=>[p,sha(readFileSync('service/src/'+p))])),started_at:new Date().toISOString(),scope:'Six assistant-authored isolated semantic controls. Expected labels and names are withheld from the model. One batch; not independent ground truth, a full pipeline run, stability measurement or accuracy score.'};
const start=performance.now();
try{
 const output=await new Models(config).json(TRANSITION_PROMPT,JSON.stringify(input),AbortSignal.timeout(90000),{purpose:'state_transition'});writeFileSync(join(dir,'output.json'),JSON.stringify(output)+'\n');report.output_sha256=sha(JSON.stringify(output));
 const plan=decodeTransitions(output,work);report.results=plan.decisions.map(d=>({case:cases[d.index][0],expected:cases[d.index][6],observed:d.relation,match:cases[d.index][6]===d.relation}));report.matched=report.results.filter(r=>r.match).length;report.status='valid';
}catch(e){report.status='failed';report.error={code:e.code??null,message:e.message};}
report.elapsed_ms=performance.now()-start;report.finished_at=new Date().toISOString();writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');writeFileSync('reports/round2-transition-controls-'+dir.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({run_dir:dir,status:report.status,matched:report.matched,planned:report.planned,error:report.error}));
