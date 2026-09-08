import importlib.util,json,sqlite3,tempfile,unittest
from pathlib import Path
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('b03_tail',Path(__file__).with_name('run-b03-tail-qa.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class B03TailTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'fixture.sqlite'
        self.namespace='fixture';self.user=self.namespace+':memops:B03_remember'
        self.schedule=[{'sample_id':'B03_remember','request_id':self.user+f':segment-{i+1}:0','hash':str(i)} for i in range(50)]
        self.samples=[{'sample_id':'B03_remember','benchmark':'memops','sessions':[{}]*50,'questions':[{'qid':str(i)} for i in range(6)]}]
        self.receipts={r['request_id']:{'hash':r['hash'],'receipt':{'success':True,'request_id':r['request_id'],'user_id':self.user,'session_id':r['request_id'][len(self.user)+1:].rsplit(':',1)[0]}} for r in self.schedule[:34]}
        row=self.schedule[33];self.case={'sample_id':'B03_remember','revision':33,'request_id':row['request_id'],'request_sha256':row['hash'],'request':{'user_id':self.user},'snapshot':'unused-for-success'}
        self.prefix={rid:r for rid,r in self.receipts.items() if rid!=row['request_id']}
        self.result={'request_id':row['request_id'],'http_attempts':1,'http_status':200,'response':self.receipts[row['request_id']]['receipt']}

    def test_failed_http_is_rejected_before_any_campaign_or_database_is_created(self):
        workspace=Path(self.tmp.name);args=SimpleNamespace(campaign='tail',previous='failed-probe',port=8125)
        def failed_source(_):
            return m.successful_prefix(self.path,self.case,self.prefix,{**self.result,'http_status':503},self.schedule,self.namespace)
        with patch.object(m,'ROOT',workspace),patch.object(m,'source',side_effect=failed_source):
            with self.assertRaisesRegex(ValueError,'Actual single-add HTTP 200'):m.prepare(args)
        self.assertFalse((workspace/'artifacts/tail').exists());self.assertFalse(self.path.exists())

    def test_revision34_without_exact_success_receipt_fails_but_complete_prefix_passes(self):
        with closing(sqlite3.connect(self.path)) as db,db:
            db.executescript('CREATE TABLE meta(key TEXT,value TEXT);CREATE TABLE requests(id TEXT,hash TEXT,receipt TEXT);')
            db.executemany('INSERT INTO meta VALUES (?,?)',[('user_id',self.user),('source_format','dual-source-v5-s1'),('revision','34')])
            db.executemany('INSERT INTO requests VALUES (?,?,?)',[(rid,row['hash'],json.dumps(row['receipt'])) for rid,row in self.prefix.items()])
        with self.assertRaisesRegex(ValueError,'exact receipt and revision 34'):m.successful_prefix(self.path,self.case,self.prefix,self.result,self.schedule,self.namespace)
        row=self.receipts[self.case['request_id']]
        with closing(sqlite3.connect(self.path)) as db,db:db.execute('INSERT INTO requests VALUES (?,?,?)',(self.case['request_id'],row['hash'],json.dumps(row['receipt'])))
        self.assertEqual(m.successful_prefix(self.path,self.case,self.prefix,self.result,self.schedule,self.namespace),self.receipts)
        with closing(sqlite3.connect(self.path)) as db,db:db.execute('UPDATE requests SET hash=? WHERE id=?',('changed',self.case['request_id']))
        with self.assertRaisesRegex(ValueError,'exact receipt and revision 34'):m.successful_prefix(self.path,self.case,self.prefix,self.result,self.schedule,self.namespace)

    def test_original_fifty_sessions_six_unique_questions_and_sixteen_tail_adds(self):
        samples,schedule=m.partition(self.samples,self.schedule)
        self.assertEqual((len(samples[0]['sessions']),len(samples[0]['questions']),len(schedule),len(schedule[34:])),(50,6,50,16))
        self.assertTrue(schedule[34]['request_id'].endswith(':segment-35:0'))
        with self.assertRaisesRegex(ValueError,'16 original tail'):m.partition(self.samples,self.schedule[:-1])
        with self.assertRaisesRegex(ValueError,'16 original tail'):m.partition(self.samples,self.schedule[:-1]+[self.schedule[0]])
        self.samples[0]['questions'][-1]['qid']='0'
        with self.assertRaisesRegex(ValueError,'six unique questions'):m.partition(self.samples,self.schedule)

    def test_later_successful_segment45_uses_exact45_prefix_and_five_remaining_adds(self):
        row=self.schedule[44];case={**self.case,'revision':44,'request_id':row['request_id'],'request_sha256':row['hash']}
        receipts={r['request_id']:{'hash':r['hash'],'receipt':{'success':True,'request_id':r['request_id'],'user_id':self.user,'session_id':r['request_id'][len(self.user)+1:].rsplit(':',1)[0]}} for r in self.schedule[:45]}
        prefix={rid:r for rid,r in receipts.items() if rid!=case['request_id']}
        result={**self.result,'request_id':case['request_id'],'response':receipts[case['request_id']]['receipt']}
        with closing(sqlite3.connect(self.path)) as db,db:
            db.executescript('CREATE TABLE meta(key TEXT,value TEXT);CREATE TABLE requests(id TEXT,hash TEXT,receipt TEXT);')
            db.executemany('INSERT INTO meta VALUES (?,?)',[('user_id',self.user),('source_format','dual-source-v5-s1'),('revision','45')])
            db.executemany('INSERT INTO requests VALUES (?,?,?)',[(rid,r['hash'],json.dumps(r['receipt'])) for rid,r in receipts.items()])
        actual=m.successful_prefix(self.path,case,prefix,result,self.schedule,self.namespace)
        self.assertEqual(actual,receipts);self.assertEqual(len(self.schedule)-len(actual),5)
        self.assertTrue(self.schedule[len(actual)]['request_id'].endswith(':segment-46:0'))

    def test_case_revision_must_match_exact_original_request_and_hash(self):
        for changes in [{'revision':44},{'request_id':self.schedule[34]['request_id']},{'request_sha256':'changed'}]:
            with self.assertRaisesRegex(ValueError,'exact original schedule position'):
                m.successful_prefix(self.path,{**self.case,**changes},self.prefix,self.result,self.schedule,self.namespace)
        self.assertFalse(self.path.exists(),'Misaligned provenance must fail before opening the database')

if __name__=='__main__':unittest.main()
