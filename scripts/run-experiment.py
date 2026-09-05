"""Run both HTTP benchmarks with explicit service configuration and provenance.

Requires Node on PATH, installed submodule dependencies, prepared data and local
Ollama. --reuse-ingestion sends every /add again with the original namespace,
relying only on HTTP idempotent receipts; eval never reads service storage.
"""
import argparse,json,os,pathlib,subprocess,time,urllib.request,hashlib
root=pathlib.Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--campaign',required=True);p.add_argument('--profile',required=True);p.add_argument('--split',choices=['dev','test'],default='dev');p.add_argument('--port',type=int,required=True);p.add_argument('--concurrency',type=int,default=3);p.add_argument('--reuse-ingestion');p.add_argument('--upstream-judge',action='store_true');args=p.parse_args()
spec=json.loads((root/'configs/experiments.json').read_text());profile=spec['profiles'][args.profile]
env=os.environ.copy()
for line in (root/'.env').read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1);env[k.strip()]=v.strip().strip('\"').strip("'")
env.update(spec['defaults']);env.update(profile['environment']);env.update(PORT=str(args.port),HOST='127.0.0.1')
answer_base=env['MEMORY_LLM_BASE_URL']
campaign=root/'artifacts'/args.campaign;campaign.mkdir(parents=True,exist_ok=True)
name=args.campaign+'-'+args.profile;namespace=args.campaign+'-'+(args.reuse_ingestion or args.profile)
env['MEMORY_DATA_DIR']=str(root/'service/.data'/namespace)
env['MEMORY_MODEL_AUDIT']=str(campaign/(args.profile+'-model-calls.jsonl'))
baseline=profile.get('baseline');cwd=root/'service'
if baseline:
 if args.reuse_ingestion:raise SystemExit('Baseline variants require independent ingestion')
 cwd=root/'service/baseline';env.update(BASELINE_VARIANT=baseline,BASELINE_DATA_DIR=str(cwd/'.data'/name),MEM0_DIR=str(cwd/'.data/config'),MEM0_TELEMETRY='false',MEMORY_LLM_BASE_URL='http://127.0.0.1:8767/v1')
 service_command=['node','--import','tsx','server.ts']
 safe={k:v for k,v in env.items() if (k.startswith('MEMORY_') or k.startswith('BASELINE_') or k in ['PORT','HOST']) and not any(t in k for t in ['KEY','SECRET','PASSWORD'])}
else:
 service_command=['node','dist/server.js']
 safe=json.loads(subprocess.check_output(['node','--input-type=module','-e',"import {configFromEnv} from './dist/config.js';const {llmKey,...safe}=configFromEnv();console.log(JSON.stringify(safe));"],cwd=cwd,env=env,text=True))
safe['extraction_prompt_sha256']=hashlib.sha256((root/'service/src/prompts.ts').read_bytes()).hexdigest()
safe['baseline_transport']='SSE-to-JSON; model/messages/options unchanged' if baseline else 'native SSE'
config_json=json.dumps(safe,sort_keys=True,separators=(',',':'));env['SERVICE_CONFIG_JSON']=config_json;env['SERVICE_CONFIG_SHA256']=hashlib.sha256(config_json.encode()).hexdigest()
config_file=campaign/(args.profile+'-service.json')
if config_file.exists():raise SystemExit('Experiment exists; choose a new campaign/profile')
config_file.write_text(json.dumps(safe,indent=2)+'\n')
log=open(campaign/(args.profile+'-service.log'),'w');service=subprocess.Popen(service_command,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT)
jobs=[]
try:
 for _ in range(80):
  if service.poll() is not None:raise RuntimeError('Service exited; inspect service log')
  try:
   with urllib.request.urlopen(f'http://127.0.0.1:{args.port}/health',timeout=1) as response:
    if response.status==200:break
  except Exception:time.sleep(.25)
 else:raise RuntimeError('Service did not become ready')
 for benchmark in ['locomo','memops']:
  run_id=name+'-'+benchmark;run_env=env.copy();run_env['EVAL_PYTHON']=str(root/'eval/.venv/bin/python');run_env['MEMORY_LLM_BASE_URL']=answer_base
  run_env.pop('EVALUATOR_API_BASE',None);run_env.pop('EVALUATOR_API_KEY',None)
  command=['node','dist/cli.js','run','--data',f'.data/{benchmark}-{args.split}.json','--run-id',run_id,'--memory-namespace',namespace,'--base-url',f'http://127.0.0.1:{args.port}','--concurrency',str(args.concurrency),'--judge-kind','refined-python' if benchmark=='locomo' else 'rubric']
  if benchmark=='locomo' and args.upstream_judge:
   run_env.update(EVALUATOR_API_BASE='http://127.0.0.1:8766/v1',EVALUATOR_API_KEY='local');command+=['--judge-model','qwen3:14b','--mode','upstream-reproduction']
  out=open(campaign/(args.profile+'-'+benchmark+'.log'),'w');job=subprocess.Popen(command,cwd=root/'eval',env=run_env,stdout=out,stderr=subprocess.STDOUT);jobs.append((benchmark,job,out));print(json.dumps({'event':'started','run_id':run_id,'pid':job.pid,'service_pid':service.pid}),flush=True)
 while jobs:
  for benchmark,job,out in jobs[:]:
   if job.poll() is not None:
    print(json.dumps({'event':'finished','benchmark':benchmark,'exit_code':job.returncode}),flush=True);out.close();jobs.remove((benchmark,job,out))
    if job.returncode:raise RuntimeError('Evaluator process failed')
  if jobs:time.sleep(1)
finally:
 for _,job,out in jobs:
  if job.poll() is None:job.terminate();job.wait(timeout=10)
  out.close()
 service.terminate()
 try:service.wait(timeout=10)
 except subprocess.TimeoutExpired:service.kill();service.wait()
 log.close()
