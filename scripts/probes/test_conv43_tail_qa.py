import importlib.util,json,pathlib,sqlite3,tempfile,unittest
from contextlib import closing
P=pathlib.Path(__file__).with_name('run-conv43-tail-qa.py');S=importlib.util.spec_from_file_location('conv43_tail',P);m=importlib.util.module_from_spec(S);S.loader.exec_module(m)

class Conv43TailTests(unittest.TestCase):
    def test_original_terminal_source_has_exact_38_prefix_and_five_tail(self):
        plan,schedule,receipts,snapshot,_=m.source()
        self.assertEqual((len(schedule),len(receipts),snapshot['revision'],plan['planned_questions']),(43,38,38,47))
        self.assertTrue(schedule[38]['request_id'].endswith(':session-27:0'))

    def test_exact_base_adaptation_preserves_original_locomo_and_candidate_binding(self):
        raw=m.BASE.read_bytes();changed=m.adapted_base(raw)
        self.assertIn('namespace=RESUME_NAMESPACE',changed);self.assertIn("'namespace':RESUME_NAMESPACE",changed)
        self.assertEqual(changed.count("'fresh_adds':5,'prefix_adds':38"),2)
        self.assertIn("'judge_kind':'refined-python','judge_model':'qwen3:14b'",changed)
        with self.assertRaises(ValueError):m.adapted_base(raw+b'\n')
        m.bind({'root':'/frozen-candidate','commit':'new-candidate','manifest_sha256':'exact-manifest'})
        self.assertEqual((str(m.b.CANDIDATE),m.b.COMMIT,m.b.MANIFEST_SHA),('/frozen-candidate','new-candidate','exact-manifest'))
        self.assertNotEqual(m.b.COMMIT,'8717d918160e36f4e8822d0ad80298342e474c5a')

    def test_prefix_requires_all_receipts_exact_hash_and_no_failed_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/'memory.sqlite';user='fixture:locomo:conv-43';schedule=[{'request_id':user+':session-'+str(i)+':0','hash':str(i)} for i in range(43)]
            with closing(sqlite3.connect(path)) as db,db:
                db.executescript('CREATE TABLE meta(key TEXT,value TEXT);CREATE TABLE requests(id TEXT,hash TEXT,receipt TEXT);')
                db.executemany('INSERT INTO meta VALUES (?,?)',[('user_id',user),('source_format','dual-source-v5-s1'),('revision','38')])
                db.executemany('INSERT INTO requests VALUES (?,?,?)',[(r['request_id'],r['hash'],json.dumps({'success':True,'request_id':r['request_id'],'user_id':user,'session_id':'session-'+str(i)})) for i,r in enumerate(schedule[:38])])
            self.assertEqual(len(m.receipt_state(path,schedule,'fixture',38)),38)
            with closing(sqlite3.connect(path)) as db,db:db.execute("UPDATE requests SET hash='wrong' WHERE id=?",(schedule[0]['request_id'],))
            with self.assertRaises(ValueError):m.receipt_state(path,schedule,'fixture',38)
            with closing(sqlite3.connect(path)) as db,db:db.execute('UPDATE requests SET hash=? WHERE id=?',('0',schedule[0]['request_id']));db.execute('DELETE FROM requests WHERE id=?',(schedule[37]['request_id'],))
            with self.assertRaises(ValueError):m.receipt_state(path,schedule,'fixture',38)
            with closing(sqlite3.connect(path)) as db,db:db.execute('INSERT INTO requests VALUES (?,?,?)',(schedule[38]['request_id'],schedule[38]['hash'],'{}'))
            with self.assertRaises(ValueError):m.receipt_state(path,schedule,'fixture',38)

if __name__=='__main__':unittest.main()
