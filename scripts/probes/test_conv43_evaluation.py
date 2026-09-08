import importlib.util,json,pathlib,sqlite3,tempfile,unittest
from contextlib import closing
P=pathlib.Path(__file__).with_name('run-conv43-evaluation.py');S=importlib.util.spec_from_file_location('conv43',P);m=importlib.util.module_from_spec(S);S.loader.exec_module(m)


class Conv43Tests(unittest.TestCase):
    def test_whole_original_sample_and_precise_core_preserve_locomo_judge(self):
        origin,samples,*_=m.source();m.validate_sample(samples)
        bad=json.loads(json.dumps(samples));bad[0]['questions'].pop()
        with self.assertRaises(ValueError):m.validate_sample(bad)
        bad=json.loads(json.dumps(samples));bad[0]['sessions'][0]['messages'].pop()
        with self.assertRaises(ValueError):m.validate_sample(bad)
        raw=(m.ROOT/'scripts/probes/run-priority-campaign.py').read_bytes();text=m.adapted_core(raw)
        for literal in ["'qwen3:14b' if locomo", "'refined-python' if locomo", "'upstream-reproduction' if locomo"]:self.assertIn(literal,text)
        with self.assertRaises(ValueError):m.adapted_core(raw+b'\n')

    def test_47_denominator_keeps_unrecorded_questions(self):
        _,samples,*_=m.source()
        with tempfile.TemporaryDirectory() as tmp:
            c=pathlib.Path(tmp);m.write(c/'conv43.json',samples);p={'campaign':'fixture','phases':[m.phase(c)]};out=m.summarize(p,c,{})
            self.assertEqual(out['planned_questions'],47);self.assertEqual(out['accuracy_over_planned'],0);self.assertEqual(len(out['question_statuses']),47)

    def test_43_real_receipts_required_and_changed_hash_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=pathlib.Path(tmp);m.write(c/'conv43.json',[{'questions':[]}]);run=c/'eval';run.mkdir();user='fixture:locomo:conv-43';data=c/'data';db=data/m.sha(user.encode())/'memory.sqlite';db.parent.mkdir(parents=True)
            schedule=[{'sample_id':'conv-43','request_id':user+':session-'+str(i)+':0','hash':str(i)} for i in range(43)];m.write(c/'schedule.json',schedule)
            (run/'ingest.jsonl').write_text(''.join(json.dumps({'request_id':r['request_id'],'attempt':0,'status':'ok'})+'\n' for r in schedule));(c/'http-receipts.jsonl').write_text(''.join(json.dumps({'request_id':r['request_id'],'hash':r['hash'],'status':200,'matches_schedule':True})+'\n' for r in schedule))
            with closing(sqlite3.connect(db)) as connection,connection:
                connection.executescript('CREATE TABLE meta(key TEXT,value TEXT);CREATE TABLE requests(id TEXT,hash TEXT,receipt TEXT);');connection.executemany('INSERT INTO meta VALUES (?,?)',[('user_id',user),('revision','43')]);connection.executemany('INSERT INTO requests VALUES (?,?,?)',[(r['request_id'],r['hash'],json.dumps({'success':True,'request_id':r['request_id'],'user_id':user,'session_id':'session-'+str(i)})) for i,r in enumerate(schedule)])
            plan={'namespace':'fixture','data_dir':str(data),'phases':[{'run_dir':str(run)}]};self.assertEqual(m.storage_check(plan,c)['integrity'],'pass')
            with closing(sqlite3.connect(db)) as connection,connection:connection.execute("UPDATE requests SET hash='changed' WHERE id=?",(schedule[-1]['request_id'],))
            self.assertEqual(m.storage_check(plan,c)['integrity'],'fail')


if __name__=='__main__':unittest.main()
