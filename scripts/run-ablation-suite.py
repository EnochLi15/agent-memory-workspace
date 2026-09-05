"""Run the finite planned ablation matrix, one profile at a time."""
import argparse,json,pathlib,subprocess,sys,time
root=pathlib.Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--campaign',required=True);p.add_argument('--port',type=int,default=8104);p.add_argument('--concurrency',type=int,default=1);a=p.parse_args()
profiles=[('B0',None),('B1',None),('U2','U3'),('B3','U3'),('B4','U3'),('B5','U3'),('B6','U3'),('no_lifecycle','B2'),('no_raw','U3'),('no_time','U3'),('no_hop','U3'),('no_rerank','U3')]
for profile,origin in profiles:
 destinations=[root/'eval/artifacts'/f'{a.campaign}-{profile}-{b}'/'manifest.json' for b in ['locomo','memops']]
 if all(p.exists() and json.loads(p.read_text())['status']=='finished' for p in destinations):
  print(json.dumps({'event':'already_finished','profile':profile}),flush=True);continue
 if origin:
  print(json.dumps({'event':'waiting_for_ingestion','profile':profile,'origin':origin}),flush=True);start=time.monotonic();missing=0
  while True:
   paths=[root/'eval/artifacts'/f'{a.campaign}-{origin}-{b}'/'manifest.json' for b in ['locomo','memops']]
   if all(p.exists() and json.loads(p.read_text())['status']=='finished' for p in paths):break
   # Do not wait forever on a stale manifest or silently restart another run.
   processes=subprocess.check_output(['ps','-axo','command'],text=True)
   missing=0 if f'--run-id {a.campaign}-{origin}-' in processes else missing+1
   if missing>=3 or time.monotonic()-start>10800:raise SystemExit('Original ingestion is unavailable; inspect its process and artifacts')
   time.sleep(2)
 command=[sys.executable,str(root/'scripts/run-experiment.py'),'--campaign',a.campaign,'--profile',profile,'--port',str(a.port),'--concurrency',str(a.concurrency)]
 if origin:command+=['--reuse-ingestion',origin]
 print(json.dumps({'event':'profile_started','profile':profile}),flush=True);subprocess.run(command,cwd=root,check=True)
print(json.dumps({'event':'ablation_matrix_finished','campaign':a.campaign}),flush=True)
