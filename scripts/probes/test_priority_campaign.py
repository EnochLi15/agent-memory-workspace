import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('priority_campaign', Path(__file__).with_name('run-priority-campaign.py'))
campaign = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(campaign)


def dataset(benchmark, count, questions, prefix):
    result = []
    for i in range(count):
        n = questions // count + (i < questions % count)
        result.append({'sample_id': f'{prefix}-{i}', 'benchmark': benchmark,
                       'sessions': [{'session_id': 's', 'messages': [{'content': 'hello'}]}],
                       'questions': [{'qid': f'{prefix}-{i}-{j}'} for j in range(n)]})
    return result


class PriorityCampaignTests(unittest.TestCase):
    def setUp(self):
        self.phases = [{'id': name, 'benchmark': benchmark, 'questions': questions, 'samples': count,
                        'run_id': name, 'run_dir': '/tmp/' + name, 'dataset_sha256': 'hash'}
                       for name, benchmark, questions, count in campaign.PHASES]
        self.datasets = [dataset(p['benchmark'], p['samples'], p['questions'], p['id']) for p in self.phases]
        self.phase, self.samples = self.phases[0], self.datasets[0]
        self.manifest = {'status': 'finished', 'run_id': self.phase['run_id'], 'memory_namespace': 'test',
                         'planned_questions': 37, 'dataset_sha256': 'hash',
                         'samples': [s['sample_id'] for s in self.samples]}
        self.ingest = [{'request_id': request, 'status': 'ok'} for request in campaign.expected_requests(self.samples, 'test')]
        self.judgments = [{'qid': q['qid'], 'sample_id': sample['sample_id'], 'status': 'judged', 'correct': True}
                          for sample in self.samples for q in sample['questions']]

    def gate(self):
        return campaign.ingestion_gate(self.manifest, self.ingest, self.judgments, self.phase, self.samples, 'test')

    def test_three_phase_partition_is_complete_and_disjoint(self):
        campaign.validate_partition(self.phases, self.datasets)
        self.assertEqual(sum(len(s['questions']) for d in self.datasets for s in d), 972)
        self.assertEqual(self.gate()['status'], 'pass')

    def test_duplicate_qid_is_rejected_across_different_benchmarks(self):
        self.datasets[2][0]['questions'][0]['qid'] = self.datasets[0][0]['questions'][0]['qid']
        with self.assertRaisesRegex(ValueError, 'Duplicate qid'):
            campaign.validate_partition(self.phases, self.datasets)

    def test_duplicate_sample_cannot_replay_ingestion_in_later_phase(self):
        self.datasets[1][0]['sample_id'] = self.datasets[0][0]['sample_id']
        with self.assertRaisesRegex(ValueError, 'Duplicate sample'):
            campaign.validate_partition(self.phases, self.datasets)

    def test_phase_order_cannot_skip_high_risk_gate(self):
        with self.assertRaisesRegex(ValueError, 'Phase order'):
            campaign.validate_partition(self.phases[::-1], self.datasets[::-1])

    def test_pipeline_and_judge_errors_do_not_become_ingestion_failures(self):
        self.judgments[0].update(status='pipeline_error', correct=False, error='Answer provider unavailable')
        self.judgments[1].update(status='judge_error', correct=False, error='Judge unavailable')
        result = self.gate()
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['question_status_counts']['pipeline_error'], 1)
        self.assertEqual(result['question_status_counts']['judge_error'], 1)

    def test_service_error_blocks_even_when_receipts_appear_ok(self):
        self.judgments[0].update(status='service_error', correct=False)
        result = self.gate()
        self.assertEqual(result['status'], 'fail')
        self.assertIn('service_error_samples', result['reasons'])

    def test_only_final_transport_outcome_controls_ingestion(self):
        request = self.ingest[0]['request_id']
        self.ingest.insert(0, {'request_id': request, 'status': 'retrying'})
        self.assertEqual(self.gate()['status'], 'pass')
        self.ingest.append({'request_id': request, 'status': 'failed'})
        self.assertIn('final_ingest_non_ok', self.gate()['reasons'])

    def test_missing_receipt_and_missing_question_prevent_gate_pass(self):
        self.ingest.pop()
        self.judgments.pop()
        reasons = self.gate()['reasons']
        self.assertIn('missing_ingest_receipts', reasons)
        self.assertIn('judgment_set_incomplete_or_duplicated', reasons)

    def test_duplicate_question_is_not_resampled_into_gate_pass(self):
        self.judgments.append(copy.deepcopy(self.judgments[0]))
        self.assertIn('judgment_set_incomplete_or_duplicated', self.gate()['reasons'])

    def test_chunk_receipts_follow_message_and_word_boundaries(self):
        samples = [{'sample_id': 'a', 'benchmark': 'memops', 'sessions': [
            {'session_id': 'first', 'messages': [{'content': 'x'}] * 21},
            {'session_id': 'second', 'messages': [{'content': 'x ' * 1999}, {'content': 'x x'}, {'content': 'x'}]}]}]
        self.assertEqual(campaign.expected_requests(samples, 'n'),
                         {'n:memops:a:first:0', 'n:memops:a:first:1', 'n:memops:a:second:0', 'n:memops:a:second:1'})

    def test_errors_and_unrecorded_questions_remain_in_972_denominator(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for phase in self.phases:
                phase['run_dir'] = str(root / phase['id'])
                Path(phase['run_dir']).mkdir()
            target = Path(self.phases[0]['run_dir'])
            (target / 'ingest.jsonl').write_text('\n'.join(json.dumps(row) for row in self.ingest) + '\n')
            records = [self.judgments[0], {**self.judgments[1], 'status': 'pipeline_error', 'correct': False}]
            (target / 'judgments.jsonl').write_text('\n'.join(json.dumps(row) for row in records) + '\n')
            (root / 'service.log').write_text(json.dumps({'event': 'degraded', 'request_id': self.ingest[0]['request_id']}) + '\n')
            result = campaign.summarize({'campaign': 'fixture', 'phases': self.phases}, root, {})
            self.assertEqual(result['correct'], 1)
            self.assertEqual(result['recorded_questions'], 2)
            self.assertEqual(result['accuracy_over_planned'], 1 / 972)
            self.assertEqual(result['phases'][0]['degraded_adds'], 1)

    def test_live_reader_ignores_only_incomplete_final_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'rows.jsonl'
            path.write_text('{"status":"ok"}\n{"status":')
            self.assertEqual(campaign.read_rows(path, live=True), [{'status': 'ok'}])
            with self.assertRaises(json.JSONDecodeError):
                campaign.read_rows(path)
            path.write_text('{"status":\n')
            with self.assertRaises(json.JSONDecodeError):
                campaign.read_rows(path, live=True)


if __name__ == '__main__':
    unittest.main()
