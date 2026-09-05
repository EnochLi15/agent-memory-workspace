"""Create portable bare repository mirrors, then prove recursive clone works."""
import pathlib,subprocess,hashlib,json,datetime
root=pathlib.Path(__file__).resolve().parents[1]
run=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
output=root/'delivery'/run;repos=output/'repositories';repos.mkdir(parents=True)
manifest={}
for folder,name in [('service','agent-memory-service.git'),('eval','agent-memory-eval.git'),('.','agent-memory-workspace.git')]:
    source=root/folder
    status=subprocess.check_output(['git','status','--porcelain'],cwd=source,text=True)
    if status.strip():raise SystemExit(f'Commit changes before bundling: {folder}')
    subprocess.run(['git','clone','--bare',str(source),str(repos/name)],check=True)
    manifest[folder]=subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()
clone=output/'recursive-clone'
subprocess.run(['git','-c','protocol.file.allow=always','clone','--recurse-submodules',str(repos/'agent-memory-workspace.git'),str(clone)],check=True)
status=subprocess.check_output(['git','submodule','status','--recursive'],cwd=clone,text=True)
if any(line.startswith(('-', '+', 'U')) for line in status.splitlines()):raise SystemExit('Recursive clone did not reproduce pinned commits')
(output/'manifest.json').write_text(json.dumps({'commits':manifest,'recursive_clone':'passed','submodules':status},indent=2)+'\n')
print(output)
