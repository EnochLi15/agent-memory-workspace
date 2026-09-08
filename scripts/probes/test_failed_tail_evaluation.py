import importlib.util
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('run-failed-tail-evaluation.py')
SPEC = importlib.util.spec_from_file_location('failed_tail_evaluation', SCRIPT)
tail = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tail)


class RecoveryBoundaries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.origin = self.root / 'origin'
        self.origin.mkdir()
        (self.origin / 'runtime.json').write_text('{}')

    def state(self, status='failed', alive=False, child=False):
        return {'status': status, 'alive': alive, 'children': {'service': {'alive': child}}}

    def gate(self):
        (self.origin / 'gate.json').write_text(json.dumps({'status': 'fail', 'phase': 'memops-risk'}))

    def test_active_controller_blocks_even_with_gate(self):
        self.gate()
        with patch.object(tail, 'read_status', return_value=self.state('running', True)):
            with self.assertRaisesRegex(ValueError, 'origin_controller_or_owned_child_alive'):
                tail.origin_state(self.origin, require_idle=True)

    def test_owned_child_blocks_after_controller_exits(self):
        self.gate()
        with patch.object(tail, 'read_status', return_value=self.state(child=True)):
            with self.assertRaisesRegex(ValueError, 'owned_child_alive'):
                tail.origin_state(self.origin, require_idle=True)

    def test_missing_gate_blocks_a_dead_controller(self):
        with patch.object(tail, 'read_status', return_value=self.state()):
            with self.assertRaisesRegex(ValueError, 'gate_missing'):
                tail.origin_state(self.origin, require_idle=True)

    def test_stopped_controller_and_failed_gate_allow_recovery(self):
        self.gate()
        with patch.object(tail, 'read_status', return_value=self.state()):
            self.assertTrue(tail.origin_state(self.origin, require_idle=True)['cloud_start_allowed'])

    def test_plan_preflight_reports_activity_without_starting_work(self):
        with patch.object(tail, 'read_status', return_value=self.state('running', True)):
            result = tail.origin_state(self.origin)
        self.assertFalse(result['cloud_start_allowed'])
        self.assertEqual(len(result['blockers']), 3)

    def test_existing_results_or_storage_are_never_reused(self):
        for item in ['runtime.json', 'runtime.launch.json', 'runtime.owner', 'data', 'service.log', 'summary.json']:
            with self.subTest(item=item):
                campaign = self.root / item.replace('.', '-')
                campaign.mkdir()
                (campaign / item).touch()
                with self.assertRaises(FileExistsError):
                    tail.fresh_outputs(campaign, self.root / 'eval')
        campaign = self.root / 'unused'; campaign.mkdir()
        evaluation = self.root / 'eval'; evaluation.mkdir()
        with self.assertRaises(FileExistsError):
            tail.fresh_outputs(campaign, evaluation)

    def test_only_own_detached_launch_can_be_consumed(self):
        campaign = self.root / 'detached'; campaign.mkdir()
        launch = campaign / 'runtime.launch.json'
        launch.write_text(json.dumps({'pid': os.getpid()}))
        tail.fresh_outputs(campaign, self.root / 'eval', allow_owned_launch=True)
        launch.write_text(json.dumps({'pid': os.getpid()+1}))
        with self.assertRaises(FileExistsError):
            tail.fresh_outputs(campaign, self.root / 'eval', allow_owned_launch=True)

    def test_path_traversal_is_rejected(self):
        for value in ['../old', '/tmp/x', 'x/y']:
            with self.assertRaises(ValueError):
                tail.name(value)

    def snapshot(self, change=None):
        path = self.root / 'before.sqlite'
        user = 'ns:memops:sample'
        schedule = [{'sample_id': 'sample', 'request_id': f'{user}:s{i}:0', 'hash': f'hash{i}'} for i in range(3)]
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript('CREATE TABLE meta(key TEXT,value TEXT); CREATE TABLE requests(id TEXT,hash TEXT,receipt TEXT);')
            db.executemany('INSERT INTO meta VALUES (?,?)', [('user_id', user), ('revision', '2'), ('source_format', 'dual-source-v5-s1')])
            db.executemany('INSERT INTO requests VALUES (?,?,?)', [(r['request_id'], r['hash'], json.dumps({'request_id':r['request_id']})) for r in schedule[:2]])
            if change:
                change(db)
        return path, schedule

    def test_exact_complete_prefix_passes_readonly_validation(self):
        path, schedule = self.snapshot()
        before = path.read_bytes()
        result = tail.snapshot_receipts(path, schedule, 'ns', 'sample', 2, 's2:0')
        self.assertEqual(len(result), 2)
        self.assertEqual(path.read_bytes(), before)

    def test_changed_payload_receipt_is_rejected(self):
        path, schedule = self.snapshot(lambda db:db.execute("UPDATE requests SET hash='different'"))
        with self.assertRaisesRegex(ValueError, 'payload differs'):
            tail.snapshot_receipts(path, schedule, 'ns', 'sample', 2, 's2:0')

    def test_wrong_namespace_is_rejected(self):
        path, schedule = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'tenant'):
            tail.snapshot_receipts(path, schedule, 'changed', 'sample', 2, 's2:0')

    def test_prefix_hole_is_rejected(self):
        path, schedule = self.snapshot(lambda db:db.execute("UPDATE requests SET id='ns:memops:sample:s2:0' WHERE id LIKE '%s1:0'"))
        with self.assertRaisesRegex(ValueError, 'complete exact pre-failure prefix'):
            tail.snapshot_receipts(path, schedule, 'ns', 'sample', 2, 's2:0')

    def test_wrong_failure_boundary_is_rejected(self):
        path, schedule = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'immediately before'):
            tail.snapshot_receipts(path, schedule, 'ns', 'sample', 2, 's99:0')

    def test_dist_manifest_detects_changes(self):
        dist = self.root / 'dist'; dist.mkdir()
        for filename in ['server.js', 'config.js', 'models.js', 'storage.js']:
            (dist / filename).write_text('export {};')
        manifest = self.root / 'manifest.json'
        manifest.write_text(json.dumps({'commit': 'fixed', 'dist': tail.dist_hashes(dist)}))
        self.assertEqual(tail.candidate_input(dist=dist, manifest=manifest)[2]['commit'], 'fixed')
        with self.assertRaisesRegex(ValueError, 'explicitly requested commit'):
            tail.candidate_input(dist=dist, manifest=manifest, expected_commit='other')
        (dist / 'storage.js').write_text('export const changed=1;')
        with self.assertRaisesRegex(ValueError, 'hash manifest'):
            tail.candidate_input(dist=dist, manifest=manifest)

    def test_launcher_observes_http_without_reading_sqlite(self):
        script = tail.service_launcher(self.root, self.root / 'service-dist')
        self.assertIn("app.addHook('onSend'", script)
        self.assertIn('isDeepStrictEqual', script)
        self.assertNotIn('TenantStore', script)
        self.assertNotIn('sqlite', script)
        file = self.root / 'launcher.mjs'; file.write_text(script)
        tail.subprocess.run(['node', '--check', str(file)], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
