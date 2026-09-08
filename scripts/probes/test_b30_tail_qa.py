import importlib.util,json,sqlite3,tempfile,unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('b30_tail',Path(__file__).with_name('run-b30-tail-qa.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class B30TailTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'fixture.sqlite'
        self.namespace='fixture';self.user=self.namespace+':memops:B30_forget'
        suffixes=[suffix for i in range(1,51) for suffix in ([f'segment-{i}:0',f'segment-{i}:1'] if i==13 else [f'segment-{i}:0'])]
        self.schedule=[{'sample_id':'B30_forget','request_id':self.user+':'+s,'hash':str(i)} for i,s in enumerate(suffixes)]
        self.samples=[{'sample_id':'B30_forget','benchmark':'memops','sessions':[{}]*50,'questions':[{'qid':str(i)} for i in range(5)]}]
        self.receipts={r['request_id']:{'hash':r['hash'],'receipt':{'success':True,'request_id':r['request_id'],'user_id':self.user,'session_id':r['request_id'][len(self.user)+1:].rsplit(':',1)[0]}} for r in self.schedule[:31]}
        row=self.schedule[30];self.case={'sample_id':'B30_forget','revision':30,'request_id':row['request_id'],'request_sha256':row['hash'],'request':{'user_id':self.user},'snapshot':'unused-for-success'}
        self.prefix={rid:r for rid,r in self.receipts.items() if rid!=row['request_id']}
        self.result={'request_id':row['request_id'],'http_attempts':1,'http_status':200,'response':self.receipts[row['request_id']]['receipt']}
    def test_previous_parameter_only_accepts_direct_workspace_artifact_children(self):
        workspace=Path(self.tmp.name).resolve();base=workspace/'artifacts';base.mkdir()
        with patch.object(m,'ROOT',workspace):
            expected=base/'priority-B30-segment30-add-20260909-02'
            self.assertEqual(m.previous_path(expected.name),expected)
            self.assertEqual(m.previous_path(str(expected)),expected)
            self.assertFalse(expected.exists(),'Path selection does not create a source campaign')
            for value in [str(workspace/'outside'),'../outside','nested/campaign']:
                with self.assertRaises(ValueError):m.previous_path(value)
            (base/'alias').symlink_to(workspace,target_is_directory=True)
            with self.assertRaises(ValueError):m.previous_path('alias')
    def test_failed_single_add_never_opens_or_accepts_a_successful_prefix(self):
        with self.assertRaisesRegex(ValueError,'Actual single-add HTTP 200'):m.successful_prefix(self.path,self.case,self.prefix,{**self.result,'http_status':503},self.schedule,self.namespace)
        self.assertFalse(self.path.exists())
    def test_missing_success_receipt_is_rejected_even_if_revision_says_31(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript('CREATE TABLE meta(key TEXT,value TEXT);CREATE TABLE requests(id TEXT,hash TEXT,receipt TEXT);')
            db.executemany('INSERT INTO meta VALUES (?,?)',[('user_id',self.user),('source_format','dual-source-v5-s1'),('revision','31')])
            db.executemany('INSERT INTO requests VALUES (?,?,?)',[(rid,row['hash'],json.dumps(row['receipt'])) for rid,row in self.prefix.items()])
        with self.assertRaisesRegex(ValueError,'exact receipt and revision 31'):m.successful_prefix(self.path,self.case,self.prefix,self.result,self.schedule,self.namespace)
        row=self.receipts[self.case['request_id']]
        with closing(sqlite3.connect(self.path)) as db, db:db.execute('INSERT INTO requests VALUES (?,?,?)',(self.case['request_id'],row['hash'],json.dumps(row['receipt'])))
        self.assertEqual(len(m.successful_prefix(self.path,self.case,self.prefix,self.result,self.schedule,self.namespace)),31)
    def test_exact_five_questions_and_twenty_remaining_adds(self):
        samples,schedule=m.partition(self.samples,self.schedule)
        self.assertEqual(len(samples[0]['questions']),5);self.assertEqual(len(schedule[31:]),20);self.assertTrue(schedule[31]['request_id'].endswith(':segment-31:0'))
        with self.assertRaisesRegex(ValueError,'20 original tail'):m.partition(self.samples,self.schedule[:-1])
        self.samples[0]['questions'].pop()
        with self.assertRaisesRegex(ValueError,'five unique questions'):m.partition(self.samples,self.schedule)

if __name__=='__main__':unittest.main()
