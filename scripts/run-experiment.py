"""Run both HTTP benchmarks with explicit service configuration and provenance.

Requires Node on PATH, installed submodule dependencies, prepared data and local
Ollama. --reuse-ingestion sends every /add again with the original namespace,
relying only on HTTP idempotent receipts; eval never reads service storage.
"""
import argparse,json,os,pathlib,subprocess,time,urllib.request,hashlib,sys
from contextlib import ExitStack
from experiment_runtime import ManagedRun, RunInterrupted, launch_detached, read_status, evaluation_schedule, run_benchmark_jobs
from experiment_identity import validate_reuse
root=pathlib.Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--campaign',required=True);p.add_argument('--trace-models',action='store_true');p.add_argument('--profile',required=True);p.add_argument('--split',choices=['dev','test'],default='dev');p.add_argument('--port',type=int);p.add_argument('--detach',action='store_true');p.add_argument('--status',action='store_true');p.add_argument('--concurrency',type=int);p.add_argument('--reuse-ingestion');p.add_argument('--upstream-judge',action='store_true');p.add_argument('--benchmark',choices=['both','locomo','memops'],default='both');p.add_argument('--locomo-data');p.add_argument('--memops-data');p.add_argument('--spec',type=pathlib.Path,default=root/'configs/experiments.json');args=p.parse_args()
campaign=root/'artifacts'/args.campaign
runtime_path=campaign/(args.profile+'-runtime.json')
if args.status:
 print(json.dumps(read_status(runtime_path),ensure_ascii=False,indent=2));sys.exit(0)
if args.port is None:p.error('--port is required to launch an experiment')
if args.detach:
 command=[sys.executable,str(pathlib.Path(__file__).resolve())]+[arg for arg in sys.argv[1:] if arg!='--detach']
 print(json.dumps(launch_detached(command,runtime_path,cwd=root),indent=2));sys.exit(0)
try:
 with ExitStack() as files, ManagedRun(runtime_path) as runtime:
  spec_bytes=args.spec.read_bytes();spec=json.loads(spec_bytes);profile=spec['profiles'][args.profile];evaluation=spec.get('evaluation',{})
  concurrency,execution=evaluation_schedule(evaluation,args.concurrency)
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
  env.pop('MEMORY_MODEL_TRACE',None)
  if args.trace_models:env['MEMORY_MODEL_TRACE']=str(campaign/(args.profile+'-model-trace.jsonl'))
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
  safe['locomo_input_override']=args.locomo_data
  safe['memops_input_override']=args.memops_data
  safe['private_model_trace_enabled']=args.trace_models
  safe['experiment_spec_sha256']=hashlib.sha256(spec_bytes).hexdigest()
  safe['evaluation_configuration']=evaluation
  safe['evaluation_execution']={'concurrency':concurrency,'benchmark_execution':execution}
  if args.reuse_ingestion:
   origins={}
   source_identity={part:{'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root/part,text=True).strip(),'dirty':bool(subprocess.check_output(['git','status','--porcelain'],cwd=root/part,text=True).strip())} for part in ['service','eval']}
   for benchmark in (['locomo','memops'] if args.benchmark=='both' else [args.benchmark]):
    origin=root/'eval/artifacts'/(namespace+'-'+benchmark);manifest_bytes=(origin/'manifest.json').read_bytes();prior=json.loads(manifest_bytes)
    if prior['status']!='finished':raise SystemExit('Wait for the original ingestion experiment to finish')
    override=args.locomo_data if benchmark=='locomo' else args.memops_data
    data_path=pathlib.Path(override or f'.data/{benchmark}-{args.split}.json')
    if not data_path.is_absolute():data_path=root/'eval'/data_path
    try:validate_reuse(prior,safe,hashlib.sha256(data_path.read_bytes()).hexdigest(),source_identity)
    except ValueError as error:raise SystemExit(str(error))
    last={row['request_id']:row for row in (json.loads(line) for line in (origin/'ingest.jsonl').read_text().splitlines())}
    if any(row['status']!='ok' for row in last.values()):raise SystemExit('Cannot reuse an incomplete ingestion for a paired retrieval ablation')
    origins[benchmark]={'run_id':prior['run_id'],'manifest_sha256':hashlib.sha256(manifest_bytes).hexdigest(),'service_commit':prior['service_commit'],'dataset_sha256':prior['dataset_sha256']}
   safe['ingestion_origin']=origins
  config_json=json.dumps(safe,sort_keys=True,separators=(',',':'));env['SERVICE_CONFIG_JSON']=config_json;env['SERVICE_CONFIG_SHA256']=hashlib.sha256(config_json.encode()).hexdigest()
  config_file=campaign/(args.profile+'-service.json')
  if config_file.exists():raise SystemExit('Experiment exists; choose a new campaign/profile')
  config_file.write_text(json.dumps(safe,indent=2)+'\n')
  if sys.platform=='darwin':
   runtime.spawn('sleep-prevention',['/usr/bin/caffeinate','-i','-w',str(os.getpid())],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  log=files.enter_context(open(campaign/(args.profile+'-service.log'),'x'));service=runtime.spawn('service',service_command,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT)
  for _ in range(80):
   runtime.poll()
   if service.poll() is not None:raise RuntimeError('Service exited; inspect service log')
   try:
    with urllib.request.urlopen(f'http://127.0.0.1:{args.port}/health',timeout=1) as response:
     if response.status==200:break
   except Exception:time.sleep(.25)
  else:raise RuntimeError('Service did not become ready')
  def launch_benchmark(benchmark):
   run_id=name+'-'+benchmark;run_env=env.copy();run_env['EVAL_PYTHON']=str(root/'eval/.venv/bin/python');run_env['MEMORY_LLM_BASE_URL']=answer_base
   run_env.pop('EVALUATOR_API_BASE',None);run_env.pop('EVALUATOR_API_KEY',None)
   override=args.locomo_data if benchmark=='locomo' else args.memops_data
   data_file=override or f'.data/{benchmark}-{args.split}.json'
   command=['node','dist/cli.js','run','--data',data_file,'--run-id',run_id,'--memory-namespace',namespace,'--base-url',f'http://127.0.0.1:{args.port}','--concurrency',str(concurrency),'--judge-kind','refined-python' if benchmark=='locomo' else 'rubric']
   if 'answer_model' in evaluation:command+=['--answer-model',evaluation['answer_model']]
   if 'judge_model' in evaluation and not (benchmark=='locomo' and args.upstream_judge):command+=['--judge-model',evaluation['judge_model']]
   if benchmark=='locomo' and args.upstream_judge:
    run_env.update(EVALUATOR_API_BASE='http://127.0.0.1:8766/v1',EVALUATOR_API_KEY='local');command+=['--judge-model','qwen3:14b','--mode','upstream-reproduction']
   out=files.enter_context(open(campaign/(args.profile+'-'+benchmark+'.log'),'x'));job=runtime.spawn(benchmark,command,cwd=root/'eval',env=run_env,stdout=out,stderr=subprocess.STDOUT);print(json.dumps({'event':'started','run_id':run_id,'pid':job.pid,'service_pid':service.pid}),flush=True)
   return benchmark,job,out
  run_benchmark_jobs(runtime,service,['locomo','memops'] if args.benchmark=='both' else [args.benchmark],launch_benchmark,execution)
except RunInterrupted as error:
 sys.exit(128+error.signum)
