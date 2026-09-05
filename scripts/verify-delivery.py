"""Independently read back a delivery archive and clone its packaged repositories."""
import argparse
import datetime
import hashlib
import json
import pathlib
import subprocess
import tarfile
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--archive', default='delivery/agent-memory-delivery.tar.gz')
parser.add_argument('--output', default='delivery/final-verification.json')
parser.add_argument('--health-url', help='Optional running service health endpoint')
args = parser.parse_args()
archive_path = pathlib.Path(args.archive).resolve()
output = pathlib.Path(args.output).resolve()


def digest(stream):
    result = hashlib.sha256()
    for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
        result.update(chunk)
    return result.hexdigest()


def git(path, *arguments):
    return subprocess.check_output(['git', *arguments], cwd=path, text=True).strip()


if output.exists():
    raise SystemExit('Preserve prior verification; choose a new output path')
with archive_path.open('rb') as stream:
    archive_sha = digest(stream)
checksum_line = archive_path.with_suffix(archive_path.suffix + '.sha256').read_text().strip()
expected_sha, expected_name = checksum_line.split(None, 1)
if archive_sha != expected_sha or expected_name != archive_path.name:
    raise SystemExit('Archive checksum file mismatch')
verified = output.parent / ('archive-verification-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
verified.mkdir(parents=True, exist_ok=False)
with tarfile.open(archive_path, 'r:gz') as archive:
    members = archive.getmembers()
    names = [member.name for member in members]
    if len(names) != len(set(names)) or any(not m.isfile() or m.name.startswith('/')
            or '..' in pathlib.PurePosixPath(m.name).parts for m in members):
        raise SystemExit('Unsafe or duplicate archive entries')
    manifest = json.load(archive.extractfile('MANIFEST.json'))
    source = json.load(archive.extractfile('source-manifest.json'))
    entries = {entry['path']: entry for entry in manifest['files']}
    if len(entries) != len(manifest['files']) or set(names) != set(entries) | {'MANIFEST.json', 'README.md'}:
        raise SystemExit('Archive and manifest inventories differ')
    for member in members:
        if member.name in entries:
            expected = entries[member.name]
            with archive.extractfile(member) as stream:
                if member.size != expected['bytes'] or digest(stream) != expected['sha256']:
                    raise SystemExit('Archive entry checksum mismatch: ' + member.name)
    readiness = json.load(archive.extractfile('reports/delivery-readiness-audit.json'))
    if readiness['status'] != 'ready_for_packaging':
        raise SystemExit('Missing prerequisite audit')
    for name, checksum in readiness['evidence_sha256'].items():
        packaged = 'evidence/' + name if name.startswith(('artifacts/', 'eval/artifacts/')) else name
        if packaged not in entries:
            continue  # Root scripts and service/eval sources live inside the Git mirrors.
        if entries[packaged]['sha256'] != checksum:
            raise SystemExit('Packaged evidence differs from readiness audit: ' + name)

# Extract in one forward-only pass. Extracting each member immediately after
# hashing it would repeatedly seek backwards through the compressed stream.
with tarfile.open(archive_path, 'r|gz') as archive:
    for member in archive:
        if member.name.startswith('repositories/') or member.name in ['MANIFEST.json', 'source-manifest.json', 'README.md']:
            archive.extract(member, verified, filter='data')

clone = verified / 'workspace'
with (verified / 'recursive-clone.log').open('w') as log:
    subprocess.run(['git', '-c', 'protocol.file.allow=always', 'clone', '--recurse-submodules',
                    str(verified / 'repositories/agent-memory-workspace.git'), str(clone)],
                   stdout=log, stderr=subprocess.STDOUT, check=True)
if source['commits'] != manifest['source_commits']:
    raise SystemExit('Source manifests disagree')
for name, commit in source['commits'].items():
    if git(clone / name, 'rev-parse', 'HEAD') != commit or git(clone / name, 'status', '--porcelain'):
        raise SystemExit('Recursive clone does not reproduce clean pinned source: ' + name)
    with (verified / ('git-fsck-' + name.replace('.', 'workspace') + '.log')).open('w') as log:
        subprocess.run(['git', 'fsck', '--full'], cwd=clone / name, stdout=log, stderr=subprocess.STDOUT, check=True)
for name, checksum in readiness['evidence_sha256'].items():
    packaged = 'evidence/' + name if name.startswith(('artifacts/', 'eval/artifacts/')) else name
    if packaged not in entries:
        with (clone / name).open('rb') as stream:
            if digest(stream) != checksum:
                raise SystemExit('Cloned source evidence mismatch: ' + name)
health = None
if args.health_url:
    with urllib.request.urlopen(args.health_url, timeout=10) as response:
        if response.status != 200:
            raise SystemExit('Running service health check failed')
        health = {'url': args.health_url, 'status': response.status}
with pathlib.Path(__file__).open('rb') as stream:
    script_sha = digest(stream)
report = {'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'status': 'complete', 'archive': str(archive_path), 'archive_bytes': archive_path.stat().st_size,
          'archive_sha256': archive_sha, 'verified_files': len(entries),
          'verification_directory': str(verified), 'source_commits': source['commits'],
          'recursive_clone_from_archive': 'passed', 'all_three_git_fsck': 'passed',
          'readiness_evidence_matches_package': True, 'service_health': health,
          'script_sha256': script_sha,
          'scope': 'Actual archive readback and packaged-source clone after completion gates. '
                   'Quality limitations and non-official scoring boundaries remain those of DELIVERY-REPORT.md; '
                   'no new functional or benchmark test run is claimed by this archive check.'}
output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(report, ensure_ascii=False))
