"""Download the official node OCI image through host networking, validate every digest,
and create a standard docker archive. Used when Docker Desktop registry mirrors stall."""
import urllib.request,json,hashlib,pathlib,gzip,tarfile,io
root=pathlib.Path(__file__).resolve().parents[1]/'delivery'/'image-cache';root.mkdir(parents=True,exist_ok=True)
repo='library/node';tag='24.18.0-bookworm-slim';registry='https://registry-1.docker.io'
with urllib.request.urlopen('https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/node:pull',timeout=30) as r:token=json.load(r)['token']
def get(path,accept=None):
 headers={'Authorization':'Bearer '+token}
 if accept:headers['Accept']=accept
 with urllib.request.urlopen(urllib.request.Request(registry+'/v2/'+repo+'/'+path,headers=headers),timeout=120) as r:return r.read(),dict(r.headers)
accept='application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'
raw,headers=get('manifests/'+tag,accept);index=json.loads(raw)
if 'manifests' in index:
 descriptor=next(m for m in index['manifests'] if m.get('platform',{}).get('architecture')=='arm64' and m['platform'].get('os')=='linux')
 raw,headers=get('manifests/'+descriptor['digest'],accept)
 assert 'sha256:'+hashlib.sha256(raw).hexdigest()==descriptor['digest']
manifest=json.loads(raw);records=[]
with tarfile.open(root/'node-24.18.0-arm64.tar','w') as archive:
 def add(name,data):
  item=tarfile.TarInfo(name);item.size=len(data);archive.addfile(item,io.BytesIO(data))
 config=None;layers=[]
 for descriptor in [manifest['config']]+manifest['layers']:
  digest=descriptor['digest'];path=root/digest.split(':')[1]
  if path.exists():data=path.read_bytes()
  else:data,_=get('blobs/'+digest);path.write_bytes(data)
  assert 'sha256:'+hashlib.sha256(data).hexdigest()==digest
  if descriptor==manifest['config']:config=path.name+'.json';add(config,data)
  else:
   name=path.name+'/layer.tar';add(name,gzip.decompress(data) if data[:2]==b'\x1f\x8b' else data);layers.append(name)
  records.append({'digest':digest,'size':len(data)});print(json.dumps(records[-1]),flush=True)
 add('manifest.json',json.dumps([{'Config':config,'RepoTags':['node:'+tag],'Layers':layers}]).encode())
(root/'manifest.json').write_text(json.dumps({'repository':registry+'/'+repo,'tag':tag,'architecture':'arm64','manifest_sha256':hashlib.sha256(raw).hexdigest(),'blobs':records},indent=2)+'\n')
print(root/'node-24.18.0-arm64.tar',flush=True)
