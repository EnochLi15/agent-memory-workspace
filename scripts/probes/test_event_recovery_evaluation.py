import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

_spec=importlib.util.spec_from_file_location('event_recovery',Path(__file__).with_name('run-event-recovery-evaluation.py'))
m=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(m)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.namespace='fixture';self.sample='B30_forget';self.user=self.namespace+':memops:'+self.sample
        self.schedule=[{'sample_id':self.sample,'request_id':self.user+':'+suffix,'hash':str(i)} for i,suffix in enumerate(['segment-1:0','segment-2:0','segment-2:1','segment-3:0'])]

    def database(self,path,count):
        db=sqlite3.connect(path)
        db.executescript('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE requests(id TEXT PRIMARY KEY,hash TEXT,receipt TEXT);')
        db.executemany('INSERT INTO meta VALUES (?,?)',[('user_id',self.user),('source_format','dual-source-v5-s1'),('revision',str(count))])
        for row in self.schedule[:count]:
            rid=row['request_id'];receipt={'success':True,'request_id':rid,'user_id':self.user,'session_id':rid[len(self.user)+1:].rsplit(':',1)[0]}
            db.execute('INSERT INTO requests VALUES (?,?,?)',(rid,row['hash'],json.dumps(receipt)))
        db.commit();db.close()

    def test_full_prefix_keeps_same_session_second_chunk_pending(self):
        path=self.root/'prefix.sqlite';self.database(path,2)
        saved=m.prefix_receipts(path,self.schedule,self.namespace,self.sample,2)
        self.assertEqual(len(saved),2);self.assertNotIn(self.schedule[2]['request_id'],saved)
        self.assertTrue(self.schedule[2]['request_id'].endswith('segment-2:1'))
        with sqlite3.connect(path) as db:db.execute('UPDATE requests SET hash=? WHERE id=?',('wrong',self.schedule[1]['request_id']))
        with self.assertRaisesRegex(ValueError,'hash or HTTP receipt'):m.prefix_receipts(path,self.schedule,self.namespace,self.sample,2)

    def test_final_database_accepts_exact_success_delta_and_rejects_receipt_for_failed_request(self):
        source=self.root/'source.sqlite';clone=self.root/'clone.sqlite';self.database(source,2);self.database(clone,3)
        snapshot={'sample_id':self.sample,'source':str(source),'source_sha256':m.sha(source.read_bytes()),'logical_state':m.fixed.logical_state(source),'clone':str(clone),'revision':2}
        ok={r['request_id'] for r in self.schedule[:3]};failed={self.schedule[3]['request_id']}
        result=m.final_database(snapshot,self.schedule,self.namespace,ok,failed)
        self.assertEqual(result['integrity'],'pass');self.assertEqual(result['new_success_receipts'],1);self.assertEqual(result['revision_after'],3)
        result=m.final_database(snapshot,self.schedule,self.namespace,ok,{self.schedule[2]['request_id']})
        self.assertIn('clone_receipt_set_or_failed_request_present',result['reasons'])
        with sqlite3.connect(clone) as db:db.execute("UPDATE meta SET value='4' WHERE key='revision'")
        self.assertIn('revision_success_receipt_increment_mismatch',m.final_database(snapshot,self.schedule,self.namespace,ok,failed)['reasons'])

    def test_standard_retry_has_one_terminal_result_and_duplicate_success_is_rejected(self):
        rows=[{'request_id':'x','attempt':0,'status':'retrying'},{'request_id':'x','attempt':1,'status':'ok'}]
        final,errors=m.terminal_ingest(rows);self.assertFalse(errors);self.assertEqual(final['x']['status'],'ok')
        rows.append({'request_id':'x','attempt':2,'status':'ok'})
        self.assertIn('multiple_or_invalid_terminal_ingest',m.terminal_ingest(rows)[1])
        self.assertTrue(m.terminal_ingest([{'request_id':'x','attempt':3,'status':'failed'}])[1])

    def test_exact_two_phases_and_frozen_entry_required(self):
        campaign=self.root/'campaign';entry=campaign/'executor/scripts/probes'/m.HERE.name
        deps={str(campaign/'executor'/p):'hash' for p in ['scripts/probes/'+m.HERE.name,'scripts/probes/run-completed-sample-qa.py','scripts/probes/run-fixed-failed-adds.py','scripts/probes/run-failed-tail-evaluation.py','scripts/experiment_runtime.py','workspace-root.json']}
        plan={'entry_point':str(entry),'data_dir':str(campaign/'data'),'candidate_dist':str(campaign/'service-dist'),'origin_service_config':str(m.ROOT/'artifacts/priority-failed-tail-20260909-01/service.json'),'port':8119,'dependencies':deps,'phases':[]}
        for id,samples,q,timeout in [('a06',['A06_forget'],5,1800),('b03-b30',['B03_remember','B30_forget'],11,21600)]:
            rid=campaign.name+'-candidate-memops-'+id
            plan['phases'].append({'id':id,'samples':samples,'planned_questions':q,'dataset':str(campaign/(id+'-samples.json')),'run_id':rid,'run_dir':str(m.ROOT/'eval/artifacts'/rid),'timeout_seconds':timeout})
        m.validate_paths(campaign,plan)
        second=plan['phases'].pop()
        with self.assertRaisesRegex(ValueError,'Exactly two'):m.validate_paths(campaign,plan)
        plan['phases'].append(second);plan['phases'].reverse()
        with self.assertRaisesRegex(ValueError,'Ordered phase'):m.validate_paths(campaign,plan)

    def test_incomplete_jsonl_record_is_preserved_and_not_parsed(self):
        path=self.root/'records.jsonl';path.write_bytes(b'{"qid":"done"}\n{"qid":"unfinished"')
        rows,partial=m.complete_rows(path);self.assertEqual(rows,[{'qid':'done'}]);self.assertTrue(partial)
        self.assertTrue(path.read_bytes().endswith(b'"unfinished"'))

    def test_failed_answer_and_judge_keep_five_question_denominator_without_blocking_ingestion(self):
        self.sample='A06_forget';self.user=self.namespace+':memops:'+self.sample
        self.schedule=[{'sample_id':self.sample,'request_id':self.user+':segment-'+str(i)+':0','hash':str(i)} for i in range(2)]
        campaign=self.root/'campaign';campaign.mkdir();run=self.root/'eval';run.mkdir()
        dataset=campaign/'a06-samples.json';dataset.write_text(json.dumps([{'sample_id':self.sample,'questions':[{'qid':'q'+str(i)} for i in range(5)]}]))
        source=self.root/'source.sqlite';self.database(source,2)
        data=campaign/'data';clone=data/m.sha(self.user.encode())/'memory.sqlite';clone.parent.mkdir(parents=True);shutil.copy2(source,clone)
        receipts=m.prefix_receipts(source,self.schedule,self.namespace,self.sample,2)
        phase={'id':'a06','samples':[self.sample],'dataset':str(dataset),'dataset_sha256':m.sha(dataset.read_bytes()),'planned_questions':5,'run_id':'fixture-run','run_dir':str(run)}
        plan={'namespace':self.namespace,'data_dir':str(data),'snapshots':[{'sample_id':self.sample,'revision':2,'source':str(source),'source_sha256':m.sha(source.read_bytes()),'logical_state':m.fixed.logical_state(source)}]}
        (campaign/'schedule.json').write_text(json.dumps(self.schedule));(campaign/'receipts.private.json').write_text(json.dumps(receipts))
        manifest={'status':'finished','run_id':phase['run_id'],'memory_namespace':self.namespace,'planned_questions':5,'dataset_sha256':phase['dataset_sha256'],'eval_commit':m.tail.EVAL_COMMIT,'answer_model':'glm-5.2','judge_model':'glm-5.2','judge_kind':'rubric','mode':'proxy'}
        (run/'manifest.json').write_text(json.dumps(manifest))
        write=lambda path,rows:path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        write(campaign/'http-receipts.jsonl',[{'request_id':r['request_id'],'status':200,'matches_schedule':True,'matches_snapshot':True} for r in self.schedule])
        write(run/'ingest.jsonl',[{'request_id':r['request_id'],'status':'ok','attempt':0} for r in self.schedule])
        write(run/'judgments.jsonl',[{'qid':'q'+str(i),'status':'pipeline_error' if i==0 else 'judge_error' if i==1 else 'judged','correct':False} for i in range(5)])
        result=m.phase_result(campaign,plan,phase)
        self.assertEqual(result['integrity'],'pass',result['reasons']);self.assertEqual(result['execution_status'],'completed',result['execution_reasons']);self.assertEqual(result['correct'],0);self.assertEqual(len(result['question_statuses']),5)


if __name__=='__main__':unittest.main()
