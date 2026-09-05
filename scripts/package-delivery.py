"""Package pinned sources, runtime and evidence after explicit completion gates."""
import argparse
import datetime
import hashlib
import io
import json
import pathlib
import subprocess
import tarfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--snapshot', required=True, help='Directory created by bundle.py')
parser.add_argument('--output', default='delivery/agent-memory-delivery.tar.gz')
parser.add_argument('--plan', action='store_true', help='Inspect file inventory without claiming completion')
args = parser.parse_args()
snapshot = pathlib.Path(args.snapshot).resolve()
output = pathlib.Path(args.output).resolve()
snapshot_manifest = json.loads((snapshot / 'manifest.json').read_text())
files = {}


def add(path, name):
    if path.is_symlink():
        raise ValueError('Symlinks are not allowed in delivery evidence: ' + str(path))
    if not path.is_file():
        return
    if path.name.startswith('.env') and path.name != '.env.example':
        raise ValueError('Environment files are not deliverables')
    if name in files:
        raise ValueError('Duplicate archive path: ' + name)
    files[name] = path


def tree(source, prefix, exclude_models=False):
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        if exclude_models and 'models' in relative.parts:
            continue
        if '__pycache__' in relative.parts or path.name == '.DS_Store':
            continue
        add(path, prefix + '/' + relative.as_posix())


tree(snapshot / 'repositories', 'repositories')
add(snapshot / 'manifest.json', 'source-manifest.json')
tree(ROOT / 'reports', 'reports')
tree(ROOT / 'docs', 'docs')
tree(ROOT / 'artifacts', 'evidence/artifacts', exclude_models=True)
tree(ROOT / 'eval/artifacts', 'evidence/eval/artifacts')
for name in ['locomo-dev.json', 'locomo-test.json', 'memops-dev.json', 'memops-test.json',
             'locomo-dev-unlabelled.json', 'locomo-dev-unlabelled.manifest.json']:
    add(ROOT / 'eval/.data' / name, 'evidence/eval/.data/' + name)
for name in ['runtime-images.tar', 'local-embedding.tar']:
    add(ROOT / 'delivery' / name, name)
inventory = {'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
             'snapshot': str(snapshot), 'source_commits': snapshot_manifest['commits'],
             'file_count': len(files), 'uncompressed_bytes': sum(p.stat().st_size for p in files.values()),
             'excluded': ['real .env files', 'recursive-clone dependencies', 'live service databases',
                          'duplicate embedding import model directories', 'Qwen Judge weights',
                          'Ollama executable', 'full upstream data downloads'],
             'files': [{'path': n, 'bytes': p.stat().st_size} for n, p in sorted(files.items())]}
if args.plan:
    plan = ROOT / 'artifacts/delivery-inventory-plan.json'
    plan.write_text(json.dumps(inventory, indent=2) + '\n')
    print(json.dumps({k: v for k, v in inventory.items() if k != 'files'}))
    raise SystemExit(0)

if output.exists() or output.with_suffix(output.suffix + '.partial').exists():
    raise SystemExit('Preserve existing delivery archive; choose a new output')
for repository in ['.', 'service', 'eval']:
    cwd = ROOT / repository
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=cwd).strip():
        raise SystemExit('Commit source and reports before packaging: ' + repository)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=cwd, text=True).strip()
    if snapshot_manifest['commits'][repository] != commit:
        raise SystemExit('Create a fresh source snapshot before packaging: ' + repository)
if not json.loads((ROOT / 'reports/completed-run-audit.json').read_text())['complete_matrix']:
    raise SystemExit('Complete all 34 runs and strict request audits first')
if not json.loads((ROOT / 'reports/model-usage-and-resources.json').read_text())['complete_evaluation_runs']:
    raise SystemExit('Complete model usage summary first')
workflow = json.loads((ROOT / 'artifacts/report-workflow.json').read_text())['results']
if len(workflow) != 3 or any(r['status'] != 'complete' for r in workflow):
    raise SystemExit('Complete posthoc reporting first')
performance_path = ROOT / 'artifacts/final-performance/summary.json'
if not performance_path.exists():
    raise SystemExit('Complete both final performance runs first')
performance = json.loads(performance_path.read_text())
if performance.get('status') != 'complete' or set(performance.get('measurements', {})) != {'primary', 'instrumented'}:
    raise SystemExit('Both final performance results must be complete')
audit = json.loads((ROOT / 'reports/final-delivery-audit.json').read_text())
if audit.get('status') != 'passed':
    raise SystemExit('A completed requirement-by-requirement delivery audit is required')

secrets = []
for line in (ROOT / '.env').read_text().splitlines():
    if line.strip() and not line.lstrip().startswith('#') and '=' in line:
        key, value = line.split('=', 1)
        value = value.strip().strip('\"').strip("'")
        if any(word in key.upper() for word in ['KEY', 'TOKEN', 'SECRET', 'PASSWORD']) and len(value) >= 16:
            secrets.append(value.encode())
overlap = max(map(len, secrets), default=1) - 1


def digest(path, check_secrets=False):
    h = hashlib.sha256()
    tail = b''
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
            if check_secrets and any(secret in tail + chunk for secret in secrets):
                raise ValueError('Configured credential found in selected artifact: ' + str(path))
            tail = (tail + chunk)[-overlap:] if overlap else b''
    return h.hexdigest()


# Git object payloads must also be inspected: compressed packfiles cannot be
# validated for credentials by a literal scan of the archive bytes alone.
git_audits = []
for repo in sorted((snapshot / 'repositories').glob('*.git')):
    objects = subprocess.check_output(['git', 'rev-list', '--objects', '--all'], cwd=repo, text=True)
    ids = [row.split()[0] for row in objects.splitlines()]
    payloads = subprocess.check_output(['git', 'cat-file', '--batch'], cwd=repo,
                                     input=('\n'.join(ids) + '\n').encode())
    if any(secret in payloads for secret in secrets):
        raise ValueError('Configured credential found in reachable source history')
    git_audits.append({'repository': repo.name, 'reachable_objects_scanned': len(ids)})

entries = []
for name, path in sorted(files.items()):
    entries.append({'path': name, 'bytes': path.stat().st_size, 'sha256': digest(path, True)})
runtime = json.loads((ROOT / 'reports/runtime-bundles.json').read_text())
by_name = {entry['path']: entry for entry in entries}
for entry in runtime['archives']:
    actual = by_name[pathlib.Path(entry['path']).name]
    if actual['sha256'] != entry['sha256'] or actual['bytes'] != entry['bytes']:
        raise ValueError('Runtime archive differs from its validated report')
manifest = {k: v for k, v in inventory.items() if k != 'files'}
manifest.update(files=entries, credential_scan='passed', git_history_scan=git_audits,
                scope='Private local delivery; no external publication. Historical failed/superseded runs '
                      'are retained and are not included in current benchmark scores.')
readme = '''# Agent Memory 交付包

先核对外部SHA256文件，再按MANIFEST.json核对各文件。保留repositories内三个bare仓库的相邻位置：

```sh
git -c protocol.file.allow=always clone --recurse-submodules repositories/agent-memory-workspace.git workspace
cp -R evidence/. workspace/
docker load -i runtime-images.tar
cd workspace
MEMORY_MODE=offline docker compose up -d --no-build --wait --wait-timeout 90
```

最终报告为workspace/reports/DELIVERY-REPORT.md。详细模型导入和增强模式配置见workspace/docs/DEPLOYMENT.md。
本包包含arm64运行镜像和本地embedding；Docker/Ollama程序、Qwen Judge权重及联网构建依赖需另行准备。
远程Answer与增强模型需在本地.env配置密钥。普通停止不要加-v，以保留数据卷。
evidence包含已保存实验与所用划分；完整上游源数据仍可按固定版本用make data获取。
历史失败产物用于审计，最终计分以报告指定的holdout-v2、dev-v6、baseline-v7为准。
'''
partial = output.with_suffix(output.suffix + '.partial')
output.parent.mkdir(parents=True, exist_ok=True)
generated = {'MANIFEST.json': (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode(),
             'README.md': readme.encode()}
with tarfile.open(partial, 'w:gz', compresslevel=1) as archive:
    for name, path in sorted(files.items()):
        archive.add(path, arcname=name, recursive=False)
    for name, data in generated.items():
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(data))
with tarfile.open(partial, 'r:gz') as archive:
    expected = {e['path']: e for e in entries}
    members = archive.getmembers()
    if len(members) != len(entries) + len(generated) or len({m.name for m in members}) != len(members):
        raise ValueError('Unexpected or repeated archive entries')
    for member in members:
        if not member.isfile() or member.name.startswith('/') or '..' in pathlib.PurePosixPath(member.name).parts:
            raise ValueError('Unsafe archive member')
        if member.name in expected:
            h = hashlib.sha256()
            with archive.extractfile(member) as stream:
                for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
                    h.update(chunk)
            entry = expected.pop(member.name)
            if entry['bytes'] != member.size or entry['sha256'] != h.hexdigest():
                raise ValueError('Artifact changed during packaging: ' + member.name)
        elif member.name not in generated or archive.extractfile(member).read() != generated[member.name]:
            raise ValueError('Generated manifest/readme verification failed')
    if expected:
        raise ValueError('Missing archive entries')
partial.rename(output)
archive_sha = digest(output)
output.with_suffix(output.suffix + '.sha256').write_text(archive_sha + '  ' + output.name + '\n')
print(json.dumps({'archive': str(output), 'bytes': output.stat().st_size,
                  'sha256': archive_sha, 'verified_files': len(entries)}))
