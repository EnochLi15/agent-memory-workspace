"""Sample resource use until a named campaign finishes; never records arguments."""
import argparse,datetime,json,pathlib,subprocess,time
p=argparse.ArgumentParser();p.add_argument('--campaign',required=True);p.add_argument('--interval',type=int,default=15);a=p.parse_args()
root=pathlib.Path(__file__).resolve().parents[1];out=root/'artifacts'/a.campaign/'resources.jsonl';out.parent.mkdir(parents=True,exist_ok=True)
while True:
 lines=subprocess.check_output(['ps','-axo','pid=,ppid=,%cpu=,rss=,command='],text=True).splitlines();processes=[]
 for line in lines:
  parts=line.strip().split(None,4)
  if len(parts)!=5:continue
  pid,ppid,cpu,rss,command=parts
  try:processes.append({'pid':int(pid),'ppid':int(ppid),'cpu':float(cpu),'rss_kib':int(rss),'command':command})
  except ValueError:continue
 selected={r['pid'] for r in processes if any(t in r['command'] for t in ['scripts/run-experiment.py','scripts/ollama-judge-server.py','scripts/streaming-gateway.py'])}
 for _ in range(5):selected|={r['pid'] for r in processes if r['ppid'] in selected}
 own=[r for r in processes if r['pid'] in selected];ollama=[r for r in processes if '/Applications/Ollama.app/' in r['command']]
 def total(rs):return {'process_count':len(rs),'rss_kib':sum(r['rss_kib'] for r in rs),'cpu_percent_sum':sum(r['cpu'] for r in rs)}
 record={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'experiments':total(own),'ollama':total(ollama),'swap':subprocess.check_output(['sysctl','-n','vm.swapusage'],text=True).strip(),'scope':'Host process RSS snapshot, shared pages may be counted more than once. Existing applications and Docker VM are not attributed to the experiment.'}
 with out.open('a') as f:f.write(json.dumps(record)+'\n')
 manifests=list((root/'eval/artifacts').glob(a.campaign+'-*/manifest.json'))
 if manifests and all(json.loads(m.read_text())['status']!='running' for m in manifests):break
 time.sleep(max(5,a.interval))
print(out)
