"""Failure-path regression tests for the existing release entry points."""
import ast
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

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


if __name__ == '__main__':
    unittest.main()
