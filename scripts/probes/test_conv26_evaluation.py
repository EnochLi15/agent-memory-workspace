import argparse,importlib.util,json,pathlib,sqlite3,tempfile,unittest
from contextlib import closing
from unittest.mock import patch
P=pathlib.Path(__file__).with_name('run-conv26-evaluation.py');S=importlib.util.spec_from_file_location('conv26',P);m=importlib.util.module_from_spec(S);S.loader.exec_module(m)


class Conv26Tests(unittest.TestCase):
    def test_complete_original_subset_order_schedule_and_locomo_protocol(self):
        origin,samples,*_=m.source();m.validate_sample(samples)
        self.assertEqual(m.COMMIT,'292ea74328d1d76aadc23f0a758a5b633300f8ba')
        self.assertEqual(m.Counter(str(q['category']) for q in samples[0]['questions']),{'4':69,'2':36,'1':24})
        for change in [lambda s:s[0]['questions'].pop(),lambda s:s[0]['sessions'][0]['messages'].pop(),lambda s:s[0]['questions'].reverse(),lambda s:s[0]['sessions'].reverse()]:
            bad=json.loads(json.dumps(samples));change(bad)
            with self.assertRaises(ValueError):m.validate_sample(bad)
        with tempfile.TemporaryDirectory() as tmp:
            data=pathlib.Path(tmp)/'conv26.json';m.write(data,samples);namespace='fixture-candidate'
            schedule=m.tail.request_schedule(pathlib.Path(origin['code_root'])/'eval',data,namespace)
            self.assertEqual(len(schedule),28);self.assertEqual(len({r['request_id'] for r in schedule}),28)
            self.assertEqual({r['request_id'] for r in schedule},m.core.expected_requests(samples,namespace))
            self.assertTrue(all(r['sample_id']=='conv-26' and r['request_id'].startswith(namespace+':locomo:conv-26:') for r in schedule))
        raw=(m.ROOT/'scripts/probes/run-priority-campaign.py').read_bytes();text=m.adapted_core(raw)
        for literal in ["'qwen3:14b' if locomo", "'refined-python' if locomo", "'upstream-reproduction' if locomo", "'--chunk-messages', '20', '--chunk-words', '2000'", "evaluation['concurrency']"]:self.assertIn(literal,text)
        with self.assertRaises(ValueError):m.adapted_core(raw+b'\n')

    def test_full_129_denominator_and_existing_campaign_refusal(self):
        _,samples,*_=m.source()
        with tempfile.TemporaryDirectory() as tmp:
            c=pathlib.Path(tmp);m.write(c/'conv26.json',samples);p={'campaign':'fixture','phases':[m.phase(c)]};out=m.summarize(p,c,{})
            self.assertEqual(out['planned_questions'],129);self.assertEqual(out['accuracy_over_planned'],0);self.assertEqual(len(out['question_statuses']),129)
            self.assertTrue(all(q['status']=='not_completed' for q in out['question_statuses']))
            self.assertEqual([q['qid'] for q in out['question_statuses']],[q['qid'] for q in samples[0]['questions']])
            existing=c/'artifacts'/'already-used';existing.mkdir(parents=True);marker=existing/'keep';marker.write_text('unchanged')
            with patch.object(m,'ROOT',c),patch.object(m,'source',side_effect=AssertionError('Refuse reuse before loading inputs')):
                with self.assertRaises(FileExistsError):m.prepare(argparse.Namespace(campaign='already-used',port=8125))
            self.assertEqual(marker.read_text(),'unchanged')

    def test_28_exact_http_success_receipts_and_revision_are_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=pathlib.Path(tmp);m.write(c/'conv26.json',[{'questions':[]}]);run=c/'eval';run.mkdir();user='fixture:locomo:conv-26';data=c/'data';db=data/m.sha(user.encode())/'memory.sqlite';db.parent.mkdir(parents=True)
            schedule=[{'sample_id':'conv-26','request_id':user+':session-'+str(i)+':0','hash':str(i)} for i in range(28)];m.write(c/'schedule.json',schedule)
            (run/'ingest.jsonl').write_text(''.join(json.dumps({'request_id':r['request_id'],'attempt':0,'status':'ok'})+'\n' for r in schedule));(c/'http-receipts.jsonl').write_text(''.join(json.dumps({'request_id':r['request_id'],'hash':r['hash'],'status':200,'matches_schedule':True})+'\n' for r in schedule))
            with closing(sqlite3.connect(db)) as connection,connection:
                connection.executescript('CREATE TABLE meta(key TEXT,value TEXT);CREATE TABLE requests(id TEXT,hash TEXT,receipt TEXT);');connection.executemany('INSERT INTO meta VALUES (?,?)',[('user_id',user),('revision','28')]);connection.executemany('INSERT INTO requests VALUES (?,?,?)',[(r['request_id'],r['hash'],json.dumps({'success':True,'request_id':r['request_id'],'user_id':user,'session_id':'session-'+str(i)})) for i,r in enumerate(schedule)])
            plan={'namespace':'fixture','data_dir':str(data),'phases':[{'run_dir':str(run)}]};result=m.storage_check(plan,c)
            self.assertEqual(result['integrity'],'pass');self.assertEqual(result['revision'],28);self.assertEqual(result['exact_receipts'],28)
            with closing(sqlite3.connect(db)) as connection,connection:connection.execute("UPDATE requests SET hash='changed' WHERE id=?",(schedule[-1]['request_id'],))
            self.assertIn('stored_receipt_hash_or_body_mismatch',m.storage_check(plan,c)['reasons'])
            with closing(sqlite3.connect(db)) as connection,connection:
                connection.execute('UPDATE requests SET hash=? WHERE id=?',(schedule[-1]['hash'],schedule[-1]['request_id']));connection.execute("UPDATE meta SET value='27' WHERE key='revision'")
            self.assertIn('revision_or_receipt_set_mismatch',m.storage_check(plan,c)['reasons'])


if __name__=='__main__':unittest.main()
