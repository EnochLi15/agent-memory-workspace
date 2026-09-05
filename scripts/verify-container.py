"""Cold-start/kill/restart checks for the service image without network access."""
import subprocess,time,json,pathlib,uuid,datetime
run_id=uuid.uuid4().hex[:12]
name='comp-memory-offline-audit-'+run_id;volume=name+'-data';image='comp-agent-memory-service:dev'
def run(*args,check=True,input=None):return subprocess.run(args,check=check,text=True,input=input,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout.strip()
existing=run('docker','ps','-a','--filter','name=^/'+name+'$','--format','{{.ID}}')
if existing:raise SystemExit('Audit container already exists; inspect before rerunning')
run('docker','volume','create',volume)
run('docker','run','-d','--name',name,'--network','none','--mount','type=volume,src='+volume+',dst=/data','-e','MEMORY_MODE=offline',image)
def wait():
 for _ in range(60):
  r=subprocess.run(['docker','exec',name,'node','-e',"fetch('http://127.0.0.1:8088/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"],capture_output=True)
  if r.returncode==0:return
  time.sleep(.25)
 raise RuntimeError('Container never became ready')
def node(code):return run('docker','exec','-i',name,'node','--input-type=module',input=code)
common="""import assert from 'node:assert/strict';const post=async(path,body)=>{const r=await fetch('http://127.0.0.1:8088'+path,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});assert.equal(r.status,200);return r.json();};"""
wait();node(common+"""await post('/add',{request_id:'a',user_id:'offline',session_id:'s',messages:[{role:'user',content:'My manager is Clara. My access code is ZZ-719.',timestamp:'2026-01-01T00:00:00Z'}]});await post('/add',{request_id:'b',user_id:'offline',session_id:'s',messages:[{role:'user',content:'Forget my access code.',timestamp:'2026-02-01T00:00:00Z'}]});""")
run('docker','kill','--signal=KILL',name);run('docker','start',name);wait()
node(common+"""const result=await post('/search',{query:'previous manager access code ZZ-719',user_id:'offline',top_k:100});assert.ok(result.data.some(x=>x.content.includes('Clara')));assert.ok(result.data.every(x=>!x.content.includes('ZZ-719')));assert.deepEqual(await post('/search',{query:'Clara',user_id:'other',top_k:100}),{data:[]});""")
run('docker','stop',name);run('docker','rm',name)
# Recreate from the image and the retained volume, with no host source mounts.
run('docker','run','-d','--name',name,'--network','none','--mount','type=volume,src='+volume+',dst=/data','-e','MEMORY_MODE=offline',image);wait()
node(common+"""const result=await post('/search',{query:'manager',user_id:'offline',top_k:100});assert.ok(result.data.some(x=>x.content.includes('Clara')));""")
run('docker','stop',name);run('docker','rm',name)
result={'run_id':run_id,'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'fresh_volume':True,'image':run('docker','image','inspect',image,'--format','{{.Id}}'),'network':'none','checks':['cold-start','synchronous-add','forget','sigkill-restart','retained-neighbor','tenant-isolation','remove-and-recreate-with-volume'],'status':'passed','retained_volume':volume}
out=pathlib.Path('reports/container-offline.json');out.parent.mkdir(exist_ok=True);out.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
