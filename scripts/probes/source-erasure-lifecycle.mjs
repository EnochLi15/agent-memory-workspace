// Fresh HTTP lifecycle with actual models; synthetic controls, no benchmark gold.
import {readFileSync,writeFileSync,mkdtempSync,appendFileSync,readdirSync} from 'node:fs';import {join,resolve} from 'node:path';import {parseEnv} from 'node:util';import {createHash} from 'node:crypto';import {createRequire} from 'node:module';
import {buildServer} from '../../service/dist/server.js';import {configFromEnv} from '../../service/dist/config.js';import {Models} from '../../service/dist/models.js';
const require=createRequire(new URL('../../service/package.json',import.meta.url)),Database=require('better-sqlite3'),sha=x=>createHash('sha256').update(x).digest('hex');
const specPath=process.argv[2]??'configs/round2-quality-source-erasure.json';
const dir=mkdtempSync(resolve('artifacts/round2-source-lifecycle-')),spec=JSON.parse(readFileSync(specPath,'utf8')),config={...configFromEnv({...parseEnv(readFileSync('.env','utf8')),...spec.defaults}),dataDir:join(dir,'data'),port:0};
process.env.MEMORY_MODEL_AUDIT=join(dir,'model-usage.jsonl');process.env.MEMORY_MODEL_TRACE=join(dir,'private-model-trace.jsonl');const original=Models.prototype.json;
Models.prototype.json=async function(system,input,signal,context){const result=await original.call(this,system,input,signal,context);appendFileSync(join(dir,'model-proposals.jsonl'),JSON.stringify({purpose:context?.purpose,input,result})+'\n');return result;};
const report={protocol:'fresh-http-source-erasure-lifecycle-v1',run_dir:dir,source_sha256:Object.fromEntries(readdirSync('service/src').filter(p=>p.endsWith('.ts')).map(p=>[p,sha(readFileSync('service/src/'+p))])),probe_sha256:sha(readFileSync(import.meta.filename)),spec_sha256:sha(readFileSync(specPath)),started_at:new Date().toISOString(),steps:[],status:'running',scope:'Four fresh HTTP adds with real extraction/verification/source scope and local embedding in the ordinary shared 90-second model budget. Exposed synthetic mechanism controls, not full benchmark/background accuracy.'};
const save=()=>writeFileSync(join(dir,'report.json'),JSON.stringify(report,null,2)+'\n');save();
const userId='source-lifecycle',app=await buildServer(config),base=await app.listen({host:'127.0.0.1',port:0});
const steps=[
 ['seed',[['user',"My dentist appointment is with Dr. Pham's office on Elm Street on April 9th. My preferred contact method is SMS."],['assistant','Your dentist visit is with Dr. Pham on Elm Street on April 9th; you prefer SMS.']]],
 ['delete',[['user','Forget my dentist appointment completely. Keep my SMS preference.']]],
 ['later_echo',[['assistant','Your appointment was with Dr. Pham on Elm Street on April 9th; you prefer SMS.']]],
 ['independent_person',[['user','My colleague Kevin has a dentist appointment with Dr. Pham on Elm Street on May 22nd. This is his appointment, not mine.']]],
];
try{
 for(const [name,messages] of steps){
  const req={request_id:name,user_id:userId,session_id:'s',messages:messages.map(([role,content],i)=>({role,content,timestamp:`2026-01-01T00:00:0${i}Z`}))},start=performance.now();
  const response=await fetch(base+'/add',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(req),signal:AbortSignal.timeout(120000)});const body=await response.json();
  const row={name,http:response.status,elapsed_ms:performance.now()-start,...(!response.ok?{error:body}:{})};report.steps.push(row);save();if(!response.ok)break;
  const db=new Database(join(config.dataDir,sha(userId),'memory.sqlite'),{readonly:true,fileMustExist:true});let state;
  try{state={revision:Number(db.prepare('SELECT value FROM meta WHERE key=?').get('revision').value),facts:db.prepare('SELECT body FROM facts').all().map(r=>JSON.parse(r.body)),messages:db.prepare('SELECT body FROM messages').all().map(r=>JSON.parse(r.body)),passages:db.prepare('SELECT body FROM passages').all().map(r=>JSON.parse(r.body))};}finally{db.close();}
  writeFileSync(join(dir,name+'-state.json'),JSON.stringify(state)+'\n');row.state_sha256=sha(readFileSync(join(dir,name+'-state.json')));
  const active=state.facts.filter(f=>f.state!=='erased'),surface=JSON.stringify({facts:active,messages:state.messages,passages:state.passages});
  row.checks={sms_preserved:active.some(f=>/sms/i.test(f.content+' '+f.value)),...(name==='delete'||name==='later_echo'?{appointment_absent:!/(?:Pham|April 9)/i.test(surface)}:{}),...(name==='independent_person'?{kevin_preserved:active.some(f=>/kevin/i.test(f.subject+' '+f.content)&&/pham/i.test(f.content)),old_appointment_absent:!/(?:April 9)/i.test(surface)}:{})};row.pass=Object.values(row.checks).every(Boolean);save();console.log(JSON.stringify(row));
 }
 report.status=report.steps.length===steps.length&&report.steps.every(s=>s.pass)?'passed':'failed';
}catch(e){report.status='failed';report.error={message:e.message};}finally{await app.close();Models.prototype.json=original;report.finished_at=new Date().toISOString();save();writeFileSync('reports/round2-source-lifecycle-'+dir.split('/').at(-1)+'.json',JSON.stringify(report,null,2)+'\n');}
console.log(JSON.stringify({run_dir:dir,status:report.status,steps:report.steps.length}));
