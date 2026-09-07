import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from query_suite import campaign_lock, command, comparable, expected_config, identifier, inspect_suite, validate_plan


class FakeTools:
    def __init__(self, runs):
        self.runs = runs
        self.audit_error = None
        self.pair_error = None

    def sha(self, path):
        return Path(path).name

    def load_run(self, directory, datasets):
        return self.runs.get(directory.name, {'status': 'not_started'})

    def origin_proof(self, run, benchmark, artifacts):
        if self.audit_error:
            raise ValueError(self.audit_error)
        return {'run_id': run['manifest']['run_id']}

    def compare(self, left, right, kind, artifacts):
        return {'status': 'refused', 'reason': self.pair_error} if self.pair_error else {'status': 'compared'}


class QuerySuiteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plan = {'protocol': 'paired-query-quality-plan-v1', 'profiles': ['sources', 'rerank'],
            'benchmarks': ['locomo', 'memops'], 'pairs': [{'baseline': 'sources', 'candidate': 'rerank', 'kind': 'query_component'}]}
        self.spec = {'defaults': {'MEMORY_RERANK': 'false'}, 'evaluation': {'answer_model': 'answer', 'judge_model': 'judge'},
            'profiles': {'sources': {'environment': {}}, 'rerank': {'environment': {'MEMORY_RERANK': 'true'}}}}
        self.config = {'rerank': False, 'tokenBudget': 6000, 'maxEvidence': 32,
            'experiment_spec_sha256': 'spec.json', 'experimental': {'multiHop': False, 'lifecycle': True}}
        self.identities = {p: {'commit': p, 'dirty': False} for p in ['service', 'eval']}
        self.runs = {}
        for b in self.plan['benchmarks']:
            self.add_run('sources', b)
        self.tools = FakeTools(self.runs)

    def add_run(self, profile, b, status='ready'):
        rid = f'campaign-{profile}-{b}'
        self.runs[rid] = {'status': status, 'questions': {'q': {'benchmark': b}}, 'manifest': {'run_id': rid, 'dataset_sha256': b + '.json',
            'service_commit': 'service', 'eval_commit': 'eval', 'answer_model': 'answer', 'judge_model': 'judge',
            'service_configuration': expected_config(self.config, self.spec, profile)}}

    def inspect(self):
        return inspect_suite(self.root, 'campaign', 'sources', self.plan, self.spec,
            self.root / 'spec.json', {b: self.root / (b + '.json') for b in self.plan['benchmarks']}, self.tools, self.identities)

    def test_concurrent_campaign_execution_is_locked_and_released(self):
        with campaign_lock(self.root, 'campaign', True):
            with self.assertRaisesRegex(ValueError, 'Another query suite'):
                with campaign_lock(self.root, 'campaign', True): pass
        with campaign_lock(self.root, 'campaign', True): pass

    def test_exact_spec_profiles_and_write_changes_rejected(self):
        validate_plan(self.plan, self.spec, 'sources')
        changed = copy.deepcopy(self.spec)
        changed['profiles']['rerank']['environment']['MEMORY_EXTRACTION_MODEL'] = 'other'
        with self.assertRaisesRegex(ValueError, 'only change'):
            validate_plan(self.plan, changed, 'sources')
        changed = copy.deepcopy(self.plan);changed['profiles'].append('missing')
        with self.assertRaisesRegex(ValueError, 'same unique'):
            validate_plan(changed, self.spec, 'sources')
        for name in ['../outside', '', 'a/b', '..']:
            with self.assertRaises(ValueError): identifier(name)

    def test_nested_query_settings_preserve_write_identity_and_budget(self):
        self.spec['profiles']['rerank']['environment']['MEMORY_EXPERIMENT_MULTI_HOP'] = 'true'
        c = expected_config(self.config, self.spec, 'rerank')
        self.assertTrue(c['rerank']);self.assertTrue(c['experimental']['multiHop'])
        self.assertTrue(c['experimental']['lifecycle']);self.assertEqual(c['tokenBudget'], 6000)
        self.assertFalse(self.config['experimental']['multiHop'])

    def test_only_proven_origin_can_ready_a_job(self):
        r = self.inspect();self.assertTrue(r['ready']);self.assertEqual(r['jobs'][0]['status'], 'ready')
        self.runs['campaign-sources-locomo']['status'] = 'running'
        r = self.inspect();self.assertFalse(r['ready']);self.assertEqual(r['jobs'][0]['status'], 'pending')
        self.assertIn('running', r['blockers'][0]['reason'])
        self.runs['campaign-sources-locomo']['status'] = 'ready'
        self.tools.audit_error = 'Incomplete planned add coverage'
        self.assertFalse(self.inspect()['ready'])

    def test_dirty_current_code_or_changed_spec_prevents_reuse(self):
        self.identities['eval']['dirty'] = True
        self.assertFalse(self.inspect()['ready'])
        self.identities['eval']['dirty'] = False
        self.runs['campaign-sources-memops']['manifest']['service_configuration']['experiment_spec_sha256'] = 'changed'
        self.assertFalse(self.inspect()['ready'])

    def test_partial_finished_or_running_profiles_are_not_skipped_or_restarted(self):
        self.add_run('rerank', 'locomo')
        r = self.inspect();self.assertFalse(r['ready']);self.assertEqual(r['jobs'][0]['status'], 'refused')
        self.add_run('rerank', 'memops', status='running')
        self.assertFalse(self.inspect()['ready'])
        self.add_run('rerank', 'memops')
        self.assertTrue(self.inspect()['ready']);self.assertEqual(self.inspect()['jobs'][0]['status'], 'complete')

    def test_finished_profile_must_match_settings_and_paired_provenance(self):
        for b in self.plan['benchmarks']: self.add_run('rerank', b)
        self.tools.pair_error = 'different ingestion'
        self.assertFalse(self.inspect()['ready'])
        self.tools.pair_error = None
        self.runs['campaign-rerank-memops']['manifest']['service_configuration']['rerank'] = False
        self.assertFalse(self.inspect()['ready'])

    def test_orphan_service_start_is_refused(self):
        p = self.root / 'artifacts/campaign/rerank-service.json';p.parent.mkdir(parents=True);p.write_text('{}')
        r = self.inspect();self.assertFalse(r['ready']);self.assertEqual(r['jobs'][0]['status'], 'refused')

    def controller(self, states, exit_code=0):
        path = Path(__file__).resolve().parents[1] / 'run-query-suite.py'
        spec = importlib.util.spec_from_file_location('query_controller_test', path)
        mod = importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        mod.__file__ = str(self.root / 'scripts/run-query-suite.py')
        for name, data in [('plan.json', self.plan), ('spec.json', self.spec)]:
            (self.root / name).write_text(json.dumps(data))
        args = ['run-query-suite.py', '--campaign', 'campaign', '--plan', str(self.root / 'plan.json'),
            '--spec', str(self.root / 'spec.json'), '--output', str(self.root / 'output'), '--execute']
        child = Mock(return_value=type('Result', (), {'returncode': exit_code})())
        with patch.object(sys, 'argv', args), patch.object(mod, 'load_tools', return_value=self.tools), \
             patch.object(mod, 'source_identity', return_value=self.identities), \
             patch.object(mod, 'inspect_suite', side_effect=copy.deepcopy(states)) as inspect, \
             patch.object(mod.subprocess, 'run', child):
            try:
                mod.main();error = None
            except SystemExit as e:
                error = str(e)
        return child, inspect, error, json.loads((self.root / 'output/suite.json').read_text())

    def test_controller_executes_once_then_revalidates_completed_profile(self):
        pending = {'ready': True, 'blockers': [], 'jobs': [{'profile': 'rerank', 'status': 'ready'}]}
        done = {'ready': True, 'blockers': [], 'jobs': [{'profile': 'rerank', 'status': 'complete'}]}
        child, inspect, error, report = self.controller([pending, done])
        self.assertIsNone(error);self.assertEqual(child.call_count, 1);self.assertEqual(inspect.call_count, 2)
        self.assertTrue(report['complete']);self.assertEqual(len(report['launched']), 1)

    def test_controller_preflight_or_child_failure_cannot_schedule_more_work(self):
        blocked = {'ready': False, 'blockers': [{'reason': 'incomplete ingestion'}],
            'jobs': [{'profile': 'rerank', 'status': 'pending'}]}
        child, inspect, error, report = self.controller([blocked])
        self.assertEqual(child.call_count, 0);self.assertIn('refused', error);self.assertFalse(report['complete'])
        import shutil
        shutil.rmtree(self.root / 'output')
        pending = {'ready': True, 'blockers': [], 'jobs': [{'profile': 'rerank', 'status': 'ready'}]}
        child, inspect, error, report = self.controller([pending], exit_code=1)
        self.assertEqual(child.call_count, 1);self.assertEqual(inspect.call_count, 1)
        self.assertIn('no automatic restart', error);self.assertFalse(report['ready'])
        self.assertEqual(report['launched'][0]['exit_code'], 1)

    def test_commands_use_same_origin_and_do_not_invent_dataset_overrides(self):
        c = command(self.root, 'campaign', 'sources', 'rerank', self.root / 'spec.json', 8117, 1,
            {'locomo': None, 'memops': 'custom path.json'}, True)
        self.assertEqual(c[c.index('--reuse-ingestion') + 1], 'sources')
        self.assertNotIn('--locomo-data', c);self.assertEqual(c[c.index('--memops-data') + 1], 'custom path.json')
        self.assertIn('--trace-models', c);self.assertNotIn('--resume', c)


if __name__ == '__main__': unittest.main()
