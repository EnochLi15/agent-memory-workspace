import importlib.util
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('completed_qa',Path(__file__).with_name('run-completed-sample-qa.py'))
qa=importlib.util.module_from_spec(spec);spec.loader.exec_module(qa)

class CompletedSampleQA(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.namespace='fixture';self.user=self.namespace+':memops:'+qa.SAMPLE
        self.schedule=[{'sample_id':qa.SAMPLE,'request_id':self.user+f':session-{i}:0','hash':f'hash-{i}'} for i in range(qa.ADDS)]
        self.saved={r['request_id']:{'hash':r['hash'],'receipt':{'success':True,'request_id':r['request_id'],'user_id':self.user,'session_id':f'session-{i}'}} for i,r in enumerate(self.schedule)}
        self.sample={'sample_id':qa.SAMPLE,'benchmark':'memops','sessions':[{'session_id':str(i),'messages':[]} for i in range(qa.SESSIONS)],'questions':[{'qid':str(i)} for i in range(qa.QUESTIONS)]}

    def write(self,p,value):
        p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value)+'\n')

    def lines(self,p,records):
        p.parent.mkdir(parents=True,exist_ok=True);p.write_text(''.join(json.dumps(r)+'\n' for r in records))

    def database(self,p):
        p.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(p)) as db,db:
            db.executescript('CREATE TABLE meta(key TEXT,value TEXT);CREATE TABLE requests(id TEXT,hash TEXT,receipt TEXT);CREATE TABLE facts(id TEXT,content TEXT);')
            db.executemany('INSERT INTO meta VALUES (?,?)',[('user_id',self.user),('revision','51'),('source_format','dual-source-v5-s1')])
            db.executemany('INSERT INTO requests VALUES (?,?,?)',[(rid,r['hash'],json.dumps(r['receipt'])) for rid,r in self.saved.items()])
            db.execute('INSERT INTO facts VALUES (?,?)',('f','original fact'))

    def test_only_the_complete_unique_sample_is_selected(self):
        self.assertEqual(qa.select_sample([{'sample_id':'failed-other'},self.sample]),[self.sample])
        for bad in [[self.sample,self.sample],[{**self.sample,'sessions':self.sample['sessions'][:-1]}],[{**self.sample,'questions':[{'qid':'duplicate'}]*5}]]:
            with self.assertRaises(ValueError):qa.select_sample(bad)

    def test_partial_family_does_not_hide_incomplete_or_repeated_sample_adds(self):
        records=[{**r,'status':'ok'} for r in self.schedule]
        qa.complete_ingest(records+[{'sample_id':'B14_forget','request_id':'other','status':'failed'}],self.schedule)
        for bad in [records[:-1],records+[records[0]],[{**r,'status':'failed' if i==3 else 'ok'} for i,r in enumerate(records)]]:
            with self.assertRaises(ValueError):qa.complete_ingest(bad,self.schedule)

    def test_complete_receipts_require_exact_payload_and_response_identity(self):
        p=self.root/'db.sqlite';self.database(p)
        self.assertEqual(qa.receipts(p,self.schedule,self.namespace),self.saved)
        with closing(sqlite3.connect(p)) as db,db:db.execute('UPDATE requests SET hash=? WHERE id=?',('changed',self.schedule[0]['request_id']))
        with self.assertRaisesRegex(ValueError,'payload hash'):qa.receipts(p,self.schedule,self.namespace)

    def test_incomplete_jsonl_and_existing_results_are_rejected(self):
        p=self.root/'records';p.write_text('{"status":"ok"}\n{"unfinished":')
        with self.assertRaisesRegex(ValueError,'Incomplete JSONL'):qa.rows(p)
        for name in ['data','summary.json','runtime.json','search-vectors.private.jsonl']:
            c=self.root/name.replace('.','-');c.mkdir();(c/name).touch()
            with self.assertRaises(FileExistsError):qa.fresh(c,c/'evaluation')

    def fixture_result(self):
        c=self.root/'campaign';c.mkdir();o=self.root/'original.sqlite';self.database(o)
        clone=c/'data'/qa.sha(self.user.encode())/'memory.sqlite';clone.parent.mkdir(parents=True);shutil.copy2(o,clone)
        self.write(c/'samples.json',[self.sample]);self.write(c/'schedule.json',self.schedule);self.write(c/'receipts.private.json',self.saved)
        self.lines(c/'http-receipts.jsonl',[{'request_id':rid,'matches_snapshot':True} for rid in self.saved])
        e=c/'evaluation';e.mkdir();self.lines(e/'ingest.jsonl',[{**r,'status':'ok'} for r in self.schedule])
        self.lines(e/'judgments.jsonl',[{'qid':str(i),'status':'pipeline_error' if i==4 else 'judged','correct':i<2} for i in range(5)])
        dist=c/'dist';dist.mkdir();(dist/'test.js').write_text('frozen')
        p={'run_dir':str(e),'dataset':str(c/'samples.json'),'run_id':'fixture','namespace':self.namespace,'pinned_files':{'samples.json':qa.sha((c/'samples.json').read_bytes())},'data_dir':str(c/'data'),'origin_database':str(o),'origin_database_sha256':qa.sha(o.read_bytes()),'database_logical_state':qa.fixed.logical_state(o),'origin_files':{},'dependencies':{},'candidate_dist':str(dist),'candidate_files':qa.fixed.all_hashes(dist)}
        self.write(e/'manifest.json',{'status':'finished','run_id':'fixture','memory_namespace':self.namespace,'planned_questions':5,'dataset_sha256':p['pinned_files']['samples.json'],'eval_commit':qa.tail.EVAL_COMMIT,'answer_model':'glm-5.2','judge_model':'glm-5.2','judge_kind':'rubric','mode':'proxy'})
        return c,p,clone

    def test_question_errors_keep_five_denominator_and_rerank_is_separate(self):
        c,p,_=self.fixture_result();self.lines(c/'model-calls.jsonl',[{'kind':'generation','purpose':'rerank'},{'kind':'embedding','action':'search'}])
        result=qa.result_summary(c,p)
        self.assertEqual(result['integrity'],'pass');self.assertEqual(result['accuracy_over_planned'],2/5)
        self.assertEqual(result['question_status_counts']['pipeline_error'],1);self.assertEqual(result['write_generation_calls'],0);self.assertEqual(result['search_rerank_generation_calls'],1)
        self.lines(c/'model-calls.jsonl',[{'kind':'generation','purpose':'extraction'}]);self.assertEqual(qa.result_summary(c,p)['integrity'],'fail')

    def test_unchanged_receipts_and_revision_cannot_hide_mutated_facts(self):
        c,p,clone=self.fixture_result()
        with closing(sqlite3.connect(clone)) as db,db:db.execute("UPDATE facts SET content='changed'")
        result=qa.result_summary(c,p);self.assertFalse(result['clone_logical_state_unchanged']);self.assertEqual(result['integrity'],'fail')

    def test_search_observer_calls_once_preserves_identity_and_cannot_change_outcome(self):
        observer=self.root/'observer.mjs';observer.write_text(qa.OBSERVER)
        program="""import assert from 'node:assert/strict';import {observeSearch} from './observer.mjs';
const vectors=[[1,2,3]],texts=['query'],signal={},events=[];let calls=0,errors=0;
class Models{async embedBatch(...args){calls++;assert.equal(this,m);assert.equal(args[0],texts);assert.equal(args[2],signal);return vectors;}}
const m=new Models();observeSearch(Models,row=>events.push(row),{model:'fixture'},()=>errors++);
assert.equal(await m.embedBatch(texts,'search',signal),vectors);assert.equal(calls,1);assert.equal(events[0].vectors,vectors);assert.equal(events[0].texts,texts);
assert.equal(await m.embedBatch(texts,'add',signal),vectors);assert.equal(calls,2);assert.equal(events.length,1);
observeSearch(Models,()=>{throw Error('disk');},{},()=>errors++);assert.equal(await m.embedBatch(texts,'search',signal),vectors);assert.equal(calls,3);assert.equal(errors,1);assert.deepEqual(vectors,[[1,2,3]]);
const failure=Error('original failure');class Failing{async embedBatch(){throw failure;}}observeSearch(Failing,()=>assert.fail(),{},()=>assert.fail());await assert.rejects(new Failing().embedBatch(texts,'search',signal),e=>e===failure);
"""
        p=self.root/'test.mjs';p.write_text(program);subprocess.run(['node',str(p)],check=True,capture_output=True)
        launcher=self.root/'launcher.mjs';launcher.write_text(qa.launcher(self.root,self.root/'dist'));subprocess.run(['node','--check',str(launcher)],check=True,capture_output=True)

if __name__=='__main__':unittest.main()
