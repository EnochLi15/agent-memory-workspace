import importlib.util
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('fixed_adds', Path(__file__).with_name('run-fixed-failed-adds.py'))
fixed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixed)


class FixedAddBoundaries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_full_or_recovery_owned_child_prevents_cloud_start(self):
        recovery = self.root / 'recovery'; recovery.mkdir(); (recovery / 'runtime.json').write_text('{}')
        inputs = {'origin': str(self.root / 'full'), 'previous_recovery': str(recovery)}
        for full, state in [({'blockers': ['origin_controller_or_owned_child_alive'], 'origin_runtime_status': 'failed'}, {'status': 'failed', 'alive': False}),
                            ({'blockers': [], 'origin_runtime_status': 'failed'}, {'status': 'failed', 'alive': False, 'children': {'service': {'alive': True}}}),
                            ({'blockers': [], 'origin_runtime_status': 'failed'}, {'status': 'running', 'alive': True})]:
            with patch.object(fixed.tail, 'origin_state', return_value=full), patch.object(fixed, 'read_status', return_value=state):
                with self.assertRaisesRegex(ValueError, 'Cloud execution blocked'): fixed.idle(inputs, require=True)

    def test_missing_recovery_owner_is_not_assumed_idle(self):
        with patch.object(fixed.tail, 'origin_state', return_value={'blockers': [], 'origin_runtime_status': 'failed'}):
            result = fixed.idle({'origin': 'full', 'previous_recovery': str(self.root / 'missing')})
        self.assertFalse(result['cloud_start_allowed'])

    def test_existing_execution_or_plan_outputs_are_never_reused(self):
        for filename in ['data', 'runtime.json', 'runtime.owner', 'model-trace.jsonl', 'results.private.json', 'summary.json', 'http-results', 'plan.json', 'service-dist']:
            with self.subTest(filename=filename):
                campaign = self.root / filename.replace('.', '-'); campaign.mkdir(); (campaign / filename).touch()
                with self.assertRaises(FileExistsError): fixed.fresh(campaign, planning=True)

    def test_frozen_inputs_and_snapshots_can_preexist_without_being_overwritten(self):
        campaign = self.root / 'new'; campaign.mkdir()
        (campaign / 'inputs.private.json').write_text('immutable'); (campaign / 'snapshots').mkdir()
        fixed.fresh(campaign, planning=True)
        self.assertEqual((campaign / 'inputs.private.json').read_text(), 'immutable')

    def test_only_own_detached_launch_is_consumed(self):
        campaign = self.root / 'launch'; campaign.mkdir()
        launch = campaign / 'runtime.launch.json'; launch.write_text(json.dumps({'pid': os.getpid()}))
        fixed.fresh(campaign, allow_owned_launch=True)
        launch.write_text(json.dumps({'pid': os.getpid()+1}))
        with self.assertRaises(FileExistsError): fixed.fresh(campaign, allow_owned_launch=True)

    def test_all_compiled_files_must_match_manifest_and_commit(self):
        dist = self.root / 'dist'; dist.mkdir()
        for f in ['server.js', 'config.js', 'models.js', 'storage.js', 'models.d.ts', 'models.js.map']:
            (dist / f).write_text('export {};')
        manifest = self.root / 'manifest.json'
        manifest.write_text(json.dumps({'commit': 'fixed', 'dist': fixed.all_hashes(dist)}))
        self.assertEqual(len(fixed.candidate(dist, manifest, 'fixed')[1]), 6)
        with self.assertRaisesRegex(ValueError, 'explicitly requested commit'): fixed.candidate(dist, manifest, 'other')
        (dist / 'extra.d.ts').write_text('extra')
        with self.assertRaisesRegex(ValueError, 'every compiled file'): fixed.candidate(dist, manifest, 'fixed')
        (dist / 'extra.d.ts').unlink(); (dist / 'models.d.ts').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'): fixed.candidate(dist, manifest, 'fixed')

    def test_changed_private_input_is_rejected_before_reading_configuration(self):
        path = self.root / 'inputs.json'; path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'private input hash'): fixed.validate_inputs(path, 'wrong')

    def database(self):
        before = self.root / 'before.sqlite'
        with closing(sqlite3.connect(before)) as db, db:
            db.executescript('CREATE TABLE meta(key TEXT,value TEXT); CREATE TABLE requests(id TEXT,hash TEXT,receipt TEXT); CREATE TABLE facts(id TEXT,content TEXT,evidence BLOB);')
            db.executemany('INSERT INTO meta VALUES (?,?)', [('user_id','ns:memops:x'),('revision','2')])
            db.executemany('INSERT INTO requests VALUES (?,?,?)', [('ns:memops:x:s0:0','h0','{"request_id":"ns:memops:x:s0:0"}'),('ns:memops:x:s1:0','h1','{"request_id":"ns:memops:x:s1:0"}')])
            db.execute('INSERT INTO facts VALUES (?,?,?)', ('f', 'private fixture', b'\x00\x01'))
        after = self.root / 'after.sqlite'; shutil.copy2(before, after)
        prefix = {f'ns:memops:x:s{i}:0': {'hash':f'h{i}','receipt':{'request_id':f'ns:memops:x:s{i}:0'}} for i in range(2)}
        case = {'sample_id':'x','revision':2,'request_id':'ns:memops:x:s2:0','request_sha256':'h2','snapshot':str(before),'request':{'user_id':'ns:memops:x'}}
        result = {'request_id':case['request_id'],'http_attempts':1,'http_status':503,'response':{'error':{'code':'TEST_ERROR'}},'elapsed_ms':10}
        return before, after, prefix, case, result

    def test_failed_http_requires_no_logical_mutation_not_just_no_receipt(self):
        before, after, prefix, case, result = self.database()
        self.assertEqual(fixed.database_result(after, case, prefix, result)['integrity'], 'pass')
        with closing(sqlite3.connect(after)) as db, db: db.execute("UPDATE facts SET content='partial commit'")
        checked = fixed.database_result(after, case, prefix, result)
        self.assertTrue(checked['checks']['revision_transition'])
        self.assertFalse(checked['checks']['failed_write_logical_state_unchanged'])
        self.assertEqual(checked['integrity'], 'fail')

    def test_logical_hash_ignores_row_layout_and_preserves_blob_values(self):
        before, after, *_ = self.database()
        with closing(sqlite3.connect(after)) as db, db:
            db.execute('CREATE TABLE rearranged AS SELECT * FROM meta ORDER BY key DESC')
            db.execute('DELETE FROM meta'); db.execute('INSERT INTO meta SELECT * FROM rearranged'); db.execute('DROP TABLE rearranged')
        self.assertNotEqual(fixed.sha(before.read_bytes()), fixed.sha(after.read_bytes()))
        self.assertEqual(fixed.logical_state(before), fixed.logical_state(after))
        with closing(sqlite3.connect(after)) as db, db: db.execute('UPDATE facts SET evidence=?', (b'\x00\x02',))
        self.assertNotEqual(fixed.logical_state(before), fixed.logical_state(after))

    def test_success_requires_exact_receipt_and_revision_plus_one(self):
        _, after, prefix, case, result = self.database()
        receipt = {'request_id':case['request_id'],'accepted':True}
        result.update(http_status=200,response=receipt)
        with closing(sqlite3.connect(after)) as db, db:
            db.execute("UPDATE meta SET value='3' WHERE key='revision'")
            db.execute('INSERT INTO requests VALUES (?,?,?)', (case['request_id'],'h2',json.dumps(receipt)))
        self.assertEqual(fixed.database_result(after,case,prefix,result)['integrity'], 'pass')
        result['response'] = {'different': True}
        self.assertEqual(fixed.database_result(after,case,prefix,result)['integrity'], 'fail')

    def test_transport_failure_that_committed_is_not_claimed_as_rollback(self):
        _, after, prefix, case, result = self.database()
        result.pop('http_status'); result['transport_error']={'name':'TimeoutError'}
        with closing(sqlite3.connect(after)) as db, db:
            db.execute("UPDATE meta SET value='3' WHERE key='revision'")
            db.execute('INSERT INTO requests VALUES (?,?,?)', (case['request_id'],'h2','{}'))
        checked = fixed.database_result(after,case,prefix,result)
        self.assertEqual(checked['integrity'],'fail'); self.assertTrue(checked['receipt_present'])

    def test_single_http_attempt_keeps_503_and_does_not_retry(self):
        counts = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                counts.append(self.rfile.read(int(self.headers['Content-Length'])))
                self.send_response(503); self.send_header('content-type','application/json'); self.end_headers()
                self.wfile.write(b'{"error":{"code":"TEST_FAILURE"}}')
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        client=self.root/'client.mjs'; client.write_text(fixed.CLIENT)
        wire=self.root/'wire.json'; wire.write_text(json.dumps([{'request_id':'test','body':'{"request_id":"test"}'}]))
        output=self.root/'response.json'
        subprocess.run(['node',str(client),str(wire),'0',f'http://127.0.0.1:{server.server_port}',str(output),'2000'],check=True,capture_output=True)
        result=json.loads(output.read_text())
        self.assertEqual(result['http_status'],503); self.assertEqual(result['http_attempts'],1); self.assertEqual(len(counts),1)
        self.assertEqual(result['response']['error']['code'],'TEST_FAILURE')

    def test_generated_service_uses_only_config_and_has_no_qa_client(self):
        text=fixed.launcher(self.root/'dist')
        self.assertIn('buildServer',text); self.assertNotIn('/search',text); self.assertNotIn('TenantStore',text)
        script=self.root/'launcher.mjs';script.write_text(text)
        subprocess.run(['node','--check',str(script)],check=True,capture_output=True)

    def test_unknown_or_prefix_generation_identity_fails_summary(self):
        campaign=self.root/'summary';campaign.mkdir()
        dist=campaign/'dist';dist.mkdir()
        snapshot=campaign/'snapshot';snapshot.write_bytes(b'immutable')
        inputs=campaign/'input';inputs.write_text('{}')
        (campaign/'prefix-receipts.private.json').write_text(json.dumps({'prefix-id':{}}))
        plan={'data_dir':str(campaign/'data'),'candidate_dist':str(dist),'candidate_files':{},'inputs':str(inputs),'inputs_source':str(inputs),
              'inputs_sha256':fixed.sha(inputs.read_bytes()),'planned_adds':1,'candidate_provenance':{'commit':'fixed'}}
        case={'sample_id':'x','request_id':'allowed-id','request':{'user_id':'u'},'snapshot':str(snapshot),'snapshot_sha256':fixed.sha(snapshot.read_bytes())}
        data={'cases':[case]}
        row={'sample_id':'x','request_id':'allowed-id','http_status':200,'integrity':'pass'}
        for identity in ['allowed-id','prefix-id','unknown-id']:
            with self.subTest(identity=identity):
                (campaign/'model-trace.jsonl').write_text(json.dumps({'trace_id':'trace','identity':{'request_id':identity}})+'\n')
                (campaign/'model-calls.jsonl').write_text(json.dumps({'kind':'generation','trace_id':'trace','purpose':'extraction','outcome':'success'})+'\n')
                with patch.object(fixed,'database_result',return_value=dict(row)):
                    summary=fixed.summarize(campaign,plan,data,[{'http_status':200}])
                self.assertEqual(summary['integrity'],'pass' if identity=='allowed-id' else 'fail')
                self.assertEqual(summary['prefix_generation_calls'],int(identity=='prefix-id'))
                self.assertEqual(summary['unknown_generation_calls'],int(identity=='unknown-id'))


if __name__ == '__main__': unittest.main()
