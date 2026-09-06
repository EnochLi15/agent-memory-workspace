// Fixed historical-boundary classification inputs; expectations never sent to model.
import {readFileSync,writeFileSync,mkdtempSync} from 'node:fs';import {resolve,join} from 'node:path';import {parseEnv} from 'node:util';import {createHash} from 'node:crypto';
import {Models} from '../../service/dist/models.js';import {configFromEnv} from '../../service/dist/config.js';import {SOURCE_ERASURE_PROMPT,sourceErasureWork} from '../../service/dist/source-erasure.js';
const sha=x=>createHash('sha256').update(x).digest('hex'),dir=mkdtempSync(resolve('artifacts/round2-source-owner-controls-'));
const parent='artifacts/round2-source-lifecycle-CDcU2w/model-proposals.jsonl',rows=readFileSync(parent,'utf8').trim().split('\n').map(JSON.parse),prior=rows.filter(r=>r.purpose==='source_erasure').at(-1),boundary=JSON.parse(prior.input).CANDIDATES[0].boundary;
const config=configFromEnv({...parseEnv(readFileSync('.env','utf8')),...JSON.parse(readFileSync('configs/round2-quality-source-erasure.json','utf8')).defaults}),models=new Models(config);process.env.MEMORY_MODEL_AUDIT=join(dir,'usage.jsonl');
const report={protocol:'source-owner-controls-v1',run_dir:dir,parent_sha256:sha(readFileSync(parent)),prompt_sha256:sha(SOURCE_ERASURE_PROMPT),source_sha256:sha(readFileSync('service/src/source-erasure.ts')),started_at:new Date().toISOString(),cases:[],scope:'One fresh classification on the exact previously uncertain five-candidate input, plus six assistant-authored semantic controls. No extraction, database mutation, independent human labels or benchmark score. Preserves the original failure.'};
writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');
try{
 const start=performance.now(),out=await models.json(SOURCE_ERASURE_PROMPT,prior.input,AbortSignal.timeout(90000),{purpose:'source_erasure'});writeFileSync(join(dir,'fixed-input-output.json'),JSON.stringify(out)+'\n');
 report.fixed_input={input_sha256:sha(prior.input),prior_output:prior.result,new_output:out,elapsed_ms:performance.now()-start};
 const cases=[
 ['different_owner',"My colleague Kevin has a dentist appointment with Dr. Pham on Elm Street on May 22nd. It is his appointment, not mine.",'retain'],
 ['different_owner_unqualified',"Kevin has a dentist appointment with Dr. Pham on Elm Street.",'uncertain'],
 ['same_owner',"I was talking about my own appointment with Dr. Pham on Elm Street again.",'erase'],
 ['joint_family',"My wife and I discussed my own dentist appointment with Dr. Pham on Elm Street.",'erase'],
 ['other_relationship',"My colleague Kevin lives next to Dr. Pham on Elm Street.",'retain'],
 ['ambiguous_owner',"The appointment with Dr. Pham on Elm Street was discussed. The owner is not specified.",'uncertain']
 ];
 for(const [name,text,expected] of cases){
  const m={id:name,session_id:'s',ordinal:0,role:'user',content:text,timestamp:'2026-01-01T00:00:00Z',searchable:true};const req={request_id:name,user_id:'u',session_id:'s',messages:[m]};
  const work=sourceErasureWork(req,[],[],[],[boundary],[],[m]);if(work.candidates.length!==1)throw Error('Control candidate discovery mismatch');
  const start=performance.now(),input=JSON.stringify({CANDIDATES:work.candidates.map((c,index)=>({index,...c}))}),output=await models.json(SOURCE_ERASURE_PROMPT,input,AbortSignal.timeout(90000),{purpose:'source_erasure'});
  const parts=output?.decisions?.[0]?.parts,valid=output?.decisions?.length===1&&output.decisions[0].index===0&&Array.isArray(parts)&&parts.map(p=>p.text).join('')===text;
  const effects=valid?[...new Set(parts.map(p=>p.effect))]:[];const pass=valid&&(expected==='uncertain'?effects.includes('uncertain'):effects.length===1&&effects[0]===expected);
  report.cases.push({name,expected,pass,elapsed_ms:performance.now()-start,input_sha256:sha(input),output});writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({name,pass,effects}));
 }
}catch(e){report.error=e.message;}
report.finished_at=new Date().toISOString();report.passed=report.cases.filter(c=>c.pass).length;report.planned=6;writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');writeFileSync('reports/round2-source-owner-controls-'+dir.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({run_dir:dir,passed:report.passed,planned:report.planned}));
