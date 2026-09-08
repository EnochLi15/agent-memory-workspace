import importlib.util,json,shutil,sqlite3,tempfile,types,unittest
from contextlib import closing
from pathlib import Path

spec=importlib.util.spec_from_file_location('focus_qa',Path(__file__).with_name('run-query-focus-qa.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class CompletedFocusQATests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)
        self.namespace='fixture';self.samples=[];self.schedule=[];self.receipts={}
        for sample,total,count,_ in m.CASES:
            user=self.namespace+':memops:'+sample
            self.samples.append({'sample_id':sample,'benchmark':'memops','sessions':[{}]*50,'questions':[{'qid':sample+':'+str(i),'question':'Question '+sample+' '+str(i)} for i in range(count)]})
            for i in range(total):
                rid=user+f':segment-{i+1}:0';row={'sample_id':sample,'request_id':rid,'hash':str(i)};self.schedule.append(row)
                self.receipts[rid]={'hash':str(i),'receipt':{'success':True,'request_id':rid,'user_id':user,'session_id':f'segment-{i+1}'}}

    def test_full152_prefix_and_fixed16_denominator_reject_missing_receipt_and_duplicate_question(self):
        m.partition(self.samples,self.schedule,self.receipts)
        with self.assertRaisesRegex(ValueError,'152 complete prefix'):m.partition(self.samples,self.schedule,{k:v for k,v in list(self.receipts.items())[:-1]})
        self.samples[-1]['questions'][-1]['qid']=self.samples[0]['questions'][0]['qid']
        with self.assertRaisesRegex(ValueError,'16 questions'):m.partition(self.samples,self.schedule,self.receipts)

    def test_only_two_reviewed_core_replacements_and_original_hash_required(self):
        before=m.BASE_CORE.read_bytes();after=m.adapt_core(before)
        self.assertEqual(after.count("r.get('purpose') not in ('rerank','query_focus')"),1)
        self.assertEqual(after.count("if not unchanged:reasons.append('completed_clone_state_changed')"),1)
        with self.assertRaisesRegex(ValueError,'reviewed hash'):m.adapt_core(before+b'\n')

    def test_every_completed_clone_must_keep_all_logical_rows_even_when_receipts_still_match(self):
        patched=types.ModuleType('patched_focus_core');patched.__file__=str(m.BASE_CORE)
        exec(compile(m.adapt_core(m.BASE_CORE.read_bytes()),str(m.BASE_CORE),'exec'),patched.__dict__)
        for sample,total,_,_ in m.CASES:
            with self.subTest(sample=sample):
                original=self.path/(sample+'-original.sqlite');clone=self.path/(sample+'-clone.sqlite');user=self.namespace+':memops:'+sample
                own=[r for r in self.schedule if r['sample_id']==sample]
                with closing(sqlite3.connect(original)) as db,db:
                    db.executescript('CREATE TABLE meta(key TEXT,value TEXT);CREATE TABLE requests(id TEXT,hash TEXT,receipt TEXT);CREATE TABLE facts(id TEXT,content TEXT);')
                    db.executemany('INSERT INTO meta VALUES (?,?)',[('user_id',user),('source_format','dual-source-v5-s1'),('revision',str(total))])
                    db.executemany('INSERT INTO requests VALUES (?,?,?)',[(r['request_id'],r['hash'],json.dumps(self.receipts[r['request_id']]['receipt'])) for r in own]);db.execute("INSERT INTO facts VALUES ('fact','unchanged')")
                shutil.copy2(original,clone)
                snapshot={'sample_id':sample,'revision':total,'source':str(original),'source_sha256':m.sha(original.read_bytes()),'logical_state':m.fixed.logical_state(original),'clone':str(clone)}
                self.assertEqual(patched.final_database(snapshot,self.schedule,self.namespace,set(self.receipts),set())['integrity'],'pass')
                with closing(sqlite3.connect(clone)) as db,db:db.execute("UPDATE facts SET content='changed'")
                failed=patched.final_database(snapshot,self.schedule,self.namespace,set(self.receipts),set())
                self.assertEqual(failed['integrity'],'fail');self.assertIn('completed_clone_state_changed',failed['reasons'])

    def test_focus_is_search_only_and_cannot_hide_an_add_generation_or_embedding(self):
        sample=self.samples[0];query=sample['questions'][0]['question'];user=self.namespace+':memops:'+sample['sample_id']
        trace={'trace_id':'focus','purpose':'query_focus','identity':{'user_id':user,'request_id':'search:'+m.sha(query.encode())},'input':json.dumps({'query':query})}
        call={'kind':'generation','purpose':'query_focus','trace_id':'focus','attempt':0,'transport_attempt_limit':1,'outcome':'ok'}
        counts,reasons=m.search_generation([call,{'kind':'embedding','action':'search'}],[trace],self.samples,self.namespace)
        self.assertFalse(reasons);self.assertEqual(counts['search_query_focus_generation_calls'],1);self.assertEqual(counts['write_generation_calls'],0)
        for changed in [{'kind':'generation','purpose':'extraction'},{'kind':'embedding','action':'add'}]:
            _,reasons=m.search_generation([call,changed],[trace],self.samples,self.namespace);self.assertTrue(reasons)
        forged={**trace,'identity':{'user_id':user,'request_id':self.schedule[0]['request_id']}}
        _,reasons=m.search_generation([call],[forged],self.samples,self.namespace);self.assertIn('unknown_search_generation',reasons)
        _,reasons=m.search_generation([call],[{**trace,'input':json.dumps({'query':query,'gold':'not allowed'})}],self.samples,self.namespace);self.assertIn('query_focus_input_or_attempt_changed',reasons)


if __name__=='__main__':unittest.main()
