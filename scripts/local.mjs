import {readFileSync,existsSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {parseEnv} from 'node:util';
import {spawn} from 'node:child_process';

export const root=fileURLToPath(new URL('../',import.meta.url));
const readEnv=path=>existsSync(path)?parseEnv(readFileSync(path,'utf8')):{};
export function localEnvironment(mode,base=root,inherited=process.env){
 if(!['offline','enhanced'].includes(mode))throw Error('LOCAL_MODE must be offline or enhanced');
 const settings={...readEnv(join(base,'.env')),...readEnv(join(base,'.env.local')),...inherited};
 const profile=mode==='offline'?'release-offline.env':'v1-bigmodel-enhanced.env';
 const env={...inherited,...parseEnv(readFileSync(join(base,'configs',profile),'utf8'))};
 // Connection credentials are separate from the pinned behavior profile.
 for(const key of ['MEMORY_LLM_BASE_URL','MEMORY_LLM_API_KEY','MEMORY_EMBEDDING_BASE_URL','MEMORY_MODEL_TRACE']){
  if(settings[key]!==undefined)env[key]=settings[key];
 }
 env.HOST='127.0.0.1';
 env.PORT=settings.LOCAL_PORT??settings.MEMORY_PORT??'8088';
 if(!/^\d+$/.test(env.PORT)||+env.PORT<1024||+env.PORT>65535)throw Error('LOCAL_PORT must be an integer from 1024 to 65535');
 env.MEMORY_DATA_DIR=resolve(base,settings.LOCAL_DATA_DIR??`service/.data/local-${mode}`);
 env.MEMORY_MODEL_AUDIT=join(env.MEMORY_DATA_DIR,'model-audit.jsonl');
 env.LOCAL_INSPECT_PORT=settings.LOCAL_INSPECT_PORT??'9229';
 if(mode==='enhanced'&&!env.MEMORY_LLM_BASE_URL)throw Error('Set MEMORY_LLM_BASE_URL in .env or .env.local');
 if(mode==='offline'){
  env.MEMORY_LLM_API_KEY='';env.MEMORY_LLM_BASE_URL='';
 }
 return env;
}
export async function main(args=process.argv.slice(2)){
 const [action='start',mode='offline']=args;
 if(!['start','dev','debug'].includes(action)||args.length>2)throw Error('Usage: node scripts/local.mjs [start|dev|debug] [offline|enhanced]');
 const [major,minor]=process.versions.node.split('.').map(Number);
 if(major<24||major===24&&minor<18)throw Error('Node >=24.18.0 required; run nvm use');
 const env=localEnvironment(mode);
 const service=join(root,'service');
 if(!existsSync(join(service,'node_modules/typescript')))throw Error('Dependencies missing; run make init');
 if(action==='start'&&!existsSync(join(service,'dist/server.js')))throw Error('Build missing; run make build');
 console.log(`Local ${mode}: http://${env.HOST}:${env.PORT}; data: ${env.MEMORY_DATA_DIR}`);
 const child=spawn(process.execPath,action==='start'?['--enable-source-maps','dist/server.js']:['scripts/dev.mjs',...(action==='debug'?['--debug']:[])],{cwd:service,env,stdio:'inherit'});
 const stop=signal=>()=>child.kill(signal);
 const handlers=['SIGINT','SIGTERM'].map(s=>{const fn=stop(s);process.on(s,fn);return [s,fn];});
 await new Promise((done,reject)=>{
  child.once('error',reject);
  child.once('exit',(code,signal)=>{process.exitCode=code??(signal==='SIGINT'?130:143);done();});
 }).finally(()=>handlers.forEach(([s,fn])=>process.off(s,fn)));
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
 try{await main();}catch(error){console.error(error.message);process.exitCode=1;}
}
