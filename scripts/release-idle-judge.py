"""Release this task's local Judge weights during idle gaps; no inference changes."""
import datetime,json,pathlib,subprocess,time,urllib.request
root=pathlib.Path(__file__).resolve().parents[1];manifest=root/'eval/artifacts/holdout-v2-U3-locomo/manifest.json';log=root/'artifacts/holdout-v2/judge-residency.jsonl';idle_since=None
def record(data):
 with log.open('a') as f:f.write(json.dumps({'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),**data})+'\n')
record({'event':'policy_started','idle_seconds':45,'model':'qwen3:14b','scope':'Only residency changes; same model digest, prompts and inference parameters.'})
while True:
 commands=subprocess.check_output(['ps','-axo','comm=,args='],text=True).splitlines()
 active=any('judge_bridge.py' in line and line.strip().split(None,1)[0].lower().endswith(('python','python3','python3.13')) for line in commands)
 finished=manifest.exists() and json.loads(manifest.read_text())['status']!='running'
 if active:idle_since=None
 elif idle_since is None:idle_since=time.monotonic()
 if not active and (finished or time.monotonic()-idle_since>=45):
  try:
   with urllib.request.urlopen('http://127.0.0.1:11434/api/ps',timeout=5) as response:models=json.load(response)['models']
   if any(m['name']=='qwen3:14b' for m in models):
    request=urllib.request.Request('http://127.0.0.1:11434/api/generate',json.dumps({'model':'qwen3:14b','keep_alive':0}).encode(),{'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=30) as response:result=json.load(response)
    record({'event':'idle_unload','done':result.get('done'),'done_reason':result.get('done_reason')})
  except Exception as error:record({'event':'residency_error','error_type':type(error).__name__})
  idle_since=time.monotonic()
 if finished and not active:break
 time.sleep(10)
record({'event':'policy_finished'})
