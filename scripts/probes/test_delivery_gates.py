"""Failure-path regression tests for the existing release entry points."""
import ast
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import shutil
import subprocess
import sys

SCRIPTS = Path(__file__).resolve().parents[1]


def definitions(name):
    # Load the real functions without running the CLI's historical top-level audit.
    tree = ast.parse((SCRIPTS / name).read_text())
    tree.body = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef))]
    namespace = {}
    exec(compile(tree, str(SCRIPTS / name), 'exec'), namespace)
    return namespace


class ReadinessFailureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.ns = definitions('audit-delivery-readiness.py')
        self.ns.update(ROOT=self.root, evidence=set(), git=lambda repo, *args: '' if args[0] == 'status' else repo)
        self.release = {'protocol': 'v1-release-manifest-v1', 'version': 'test-fixture', 'human_labels': 0,
                        'commits': {'service': 'service', 'eval': 'eval'},
                        'gates': {'blockers': 'blockers.json', 'runtime': 'soak.json'}}
        for repo in ['service', 'eval']:
            self.put(repo + '/contracts/contract.json', {})
        self.output = self.root / 'result.json'

    def put(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def rejected(self, pattern):
        release = self.put('release.json', self.release)
        with self.assertRaisesRegex(SystemExit, pattern):
            self.ns['audit_v1'](release, self.output)
        self.assertFalse(self.output.exists(), 'A rejected gate must never leave a ready artifact')

    def blockers(self):
        self.put('blockers.json', {'status': 'complete', 'unresolved_p0': [],
                 'cases': [{'cause': 'fixture', 'evidence': 'fixture', 'disposition': 'fixed_and_verified'} for _ in range(19)]})

    def test_missing_failure_evidence_cannot_mark_ready(self):
        self.rejected('Required completed evidence')

    def test_unresolved_p0_cannot_mark_ready(self):
        self.put('blockers.json', {'status': 'complete', 'unresolved_p0': ['write atomicity']})
        self.rejected('Resolve known P0')

    def test_changed_bound_evidence_is_rejected(self):
        self.put('blockers.json', {'evidence_sha256': {'bound.json': '0' * 64}})
        self.put('bound.json', {'changed': True})
        self.rejected('Evidence changed')

    def test_escaping_evidence_is_rejected(self):
        self.release['gates']['blockers'] = '../outside.json'
        self.rejected('workspace-relative')

    def test_running_soak_is_not_completion(self):
        self.blockers()
        self.put('soak.json', {'passed': False, 'duration_seconds': 4000})
        self.rejected('long runtime gate')

    def test_claimed_soak_pass_cannot_hide_interrupted_record(self):
        self.blockers()
        runtime = self.put('scripts/experiment_runtime.py', 'runtime')
        runner = self.put('scripts/run-experiment.py', 'runner')
        self.put('soak.json', {'passed': True, 'duration_seconds': 7500,
                 'runtime_sha256': hashlib.sha256(runtime.read_bytes()).hexdigest(),
                 'runner_sha256': hashlib.sha256(runner.read_bytes()).hexdigest(),
                 'runtime_record': 'runtime.json'})
        self.put('runtime.json', {'status': 'interrupted', 'exit_code': None})
        self.rejected('did not exit cleanly')


class PackagingInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.ns = definitions('package-delivery.py')
        self.ns.update(release={'protocol': 'v1-release-manifest-v1'}, files={})

    def test_private_trace_database_and_env_are_rejected(self):
        for name in ['candidate-model-trace.jsonl', 'memory.sqlite', 'memory.sqlite-wal', '.env']:
            with self.subTest(name=name):
                path = self.root / name
                path.write_text('private fixture')
                with self.assertRaises(ValueError):
                    self.ns['add'](path, name)
        self.assertEqual(self.ns['files'], {})

    def test_evidence_symlink_is_rejected(self):
        source = self.root / 'source.json'
        source.write_text('{}')
        link = self.root / 'report.json'
        link.symlink_to(source)
        with self.assertRaisesRegex(ValueError, 'Symlinks'):
            self.ns['add'](link, 'report.json')

    def test_selected_report_preserves_exact_path_and_rejects_duplicate(self):
        report = self.root / 'report.json'
        report.write_text('{}')
        self.ns['add'](report, 'reports/report.json')
        self.assertEqual(self.ns['files'], {'reports/report.json': report})
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.ns['add'](report, 'reports/report.json')


class PackagedSourceRoundtripTests(unittest.TestCase):
    def test_v1_archive_roundtrip_and_changed_manifest_rejection(self):
        # Tiny synthetic Git repositories exercise the archive protocol only.
        # This fixture has no model, benchmark, deployable image or real ready report.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            log = root.parent / (root.name + '-commands.log')
            self.addCleanup(log.unlink, missing_ok=True)

            def command(*args, cwd=root):
                with log.open('a') as stream:
                    result = subprocess.run(args, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT)
                self.assertEqual(result.returncode, 0, log.read_text())

            def git(*args, cwd=root):
                return subprocess.check_output(['git', *args], cwd=cwd, text=True).strip()

            def put(name, value):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value) if not isinstance(value, str) else value)
                return path

            def sha(name):
                return hashlib.sha256((root / name).read_bytes()).hexdigest()

            commits = {}
            for part in ['service', 'eval', '.']:
                (root / part).mkdir(exist_ok=True)
                command('git', 'init', '-b', 'main', cwd=root / part)
                command('git', 'config', 'user.email', 'fixture@example.invalid', cwd=root / part)
                command('git', 'config', 'user.name', 'Archive test fixture', cwd=root / part)
                if part != '.':
                    put(part + '/README.md', 'Synthetic archive test fixture only.\n')
                    command('git', 'add', 'README.md', cwd=root / part)
                    command('git', 'commit', '-m', 'fixture', cwd=root / part)
                    commits[part] = git('rev-parse', 'HEAD', cwd=root / part)
            put('.gitignore', '.env\nartifacts/\ndelivery/\n')
            put('.env', 'TEST_API_KEY=synthetic-credential-must-never-be-packaged\n')
            put('.gitmodules', ''.join(f'[submodule "{part}"]\n path = {part}\n url = ../agent-memory-{part}.git\n'
                                      for part in ['service', 'eval']))
            for part in ['service', 'eval']:
                command('git', 'update-index', '--add', '--cacheinfo', '160000', commits[part], part)
            for name in ['bundle.py', 'package-delivery.py', 'verify-delivery.py', 'audit-delivery-readiness.py']:
                path = root / 'scripts' / name
                path.parent.mkdir(exist_ok=True)
                shutil.copyfile(SCRIPTS / name, path)
            put('delivery/fixture-images.tar', 'Not a Docker image: archive protocol fixture.\n')
            put('delivery/fixture-embedding.tar', 'Not a model: archive protocol fixture.\n')
            archives = [{'path': name, 'sha256': sha(name), 'bytes': (root / name).stat().st_size}
                        for name in ['delivery/fixture-images.tar', 'delivery/fixture-embedding.tar']]
            put('reports/runtime.json', {'archives': archives})
            release = {'protocol': 'v1-release-manifest-v1', 'version': 'test-fixture', 'commits': commits,
                       'runtime_bundles': 'reports/runtime.json', 'service_image': 'fixture:not-deployable',
                       'delivery_report': 'reports/fixture.md'}
            put('configs/release.json', release)
            put('reports/fixture.md', 'Synthetic packaging test. No release acceptance claim.\n')
            names = ['configs/release.json', 'reports/runtime.json', 'reports/fixture.md',
                     'scripts/audit-delivery-readiness.py', 'scripts/verify-delivery.py'] + [a['path'] for a in archives]
            put('reports/readiness.json', {'protocol': 'v1-readiness-audit-v1', 'status': 'ready_for_packaging',
                'scope': 'Synthetic packaging-consumer fixture; never a real project readiness audit.',
                'service_commit': commits['service'], 'eval_commit': commits['eval'], 'version': 'test-fixture',
                'release_manifest': 'configs/release.json', 'release_manifest_sha256': sha('configs/release.json'),
                'audit_script_sha256': sha('scripts/audit-delivery-readiness.py'),
                'evidence_sha256': {name: sha(name) for name in names}})
            command('git', 'add', '.')
            command('git', 'commit', '-m', 'archive protocol fixture')
            command(sys.executable, 'scripts/bundle.py')
            snapshots = [p.parent for p in (root / 'delivery').glob('*/manifest.json')]
            self.assertEqual(len(snapshots), 1)
            package_args = [sys.executable, 'scripts/package-delivery.py', '--snapshot', str(snapshots[0]),
                            '--release', 'configs/release.json', '--readiness', 'reports/readiness.json']
            command(*package_args, '--output', 'delivery/fixture.tar.gz')
            # Freeze both verifications to the same wall-clock second. Separate
            # output paths must never collide through the scratch directory name.
            frozen_clock = ("import datetime,runpy,sys\n"
                            "class FixedDateTime(datetime.datetime):\n"
                            " @classmethod\n"
                            " def now(cls,tz=None): return cls(2026,9,7,0,0,0,tzinfo=tz)\n"
                            "datetime.datetime=FixedDateTime\n"
                            "runpy.run_path(sys.argv.pop(1),run_name='__main__')\n")
            verify_command = [sys.executable, '-c', frozen_clock, 'scripts/verify-delivery.py']
            command(*verify_command, '--archive', 'delivery/fixture.tar.gz',
                    '--output', 'delivery/verification.json')
            result = json.loads((root / 'delivery/verification.json').read_text())
            self.assertEqual(result['recursive_clone_from_archive'], 'passed')
            self.assertEqual(result['all_three_git_fsck'], 'passed')
            self.assertTrue(result['readiness_evidence_matches_package'])

            # Re-signing the archive checksum cannot hide a changed release identity.
            import tarfile
            import io
            archive = root / 'delivery/fixture.tar.gz'
            changed = root / 'delivery/changed.tar.gz'
            with tarfile.open(archive, 'r:gz') as source, tarfile.open(changed, 'w:gz') as target:
                members = [(m, source.extractfile(m).read()) for m in source.getmembers()]
                for member, data in members:
                    if member.name == 'MANIFEST.json':
                        value = json.loads(data)
                        value['release_manifest_sha256'] = '0' * 64
                        data = json.dumps(value).encode()
                        member.size = len(data)
                    target.addfile(member, io.BytesIO(data))
            put('delivery/changed.tar.gz.sha256', sha('delivery/changed.tar.gz') + '  changed.tar.gz\n')
            result = subprocess.run([*verify_command, '--archive', str(changed),
                                     '--output', 'delivery/must-not-exist.json'], cwd=root, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('release identity differs', result.stderr)
            self.assertFalse((root / 'delivery/must-not-exist.json').exists())


if __name__ == '__main__':
    unittest.main()
