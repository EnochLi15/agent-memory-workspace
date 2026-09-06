import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('seal', Path(__file__).parents[1] / 'seal-unexposed-memops.py')
seal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seal)


class SealTests(unittest.TestCase):
    def fixture(self):
        samples = [{'sample_id': f'A{i:02}_{op}', 'group_id': f'A{i:02}',
                    'sessions': [{'full': 'conversation'}],
                    'questions': [{'qid': f'{i}-{op}', 'question': 'private', 'answer': 'private'}]}
                   for i in range(1, 21) for op in seal.OPERATIONS]
        audit = {'no_recorded_exposure_groups': [f'A{i:02}' for i in range(2, 21)],
                 'recorded_exposed_groups': ['A01'],
                 'no_recorded_exposure_files': [s['sample_id'] + '.json' for s in samples if s['group_id'] != 'A01']}
        return samples, audit

    def test_complete_samples_distinct_groups_and_all_operation_quotas(self):
        samples, audit = self.fixture()
        chosen = seal.select(samples, audit, 'fixed', 3)
        self.assertEqual(len(chosen), 15)
        self.assertEqual(len({s['group_id'] for s in chosen}), 15)
        self.assertNotIn('A01', {s['group_id'] for s in chosen})
        for op in seal.OPERATIONS:
            self.assertEqual(sum(s['sample_id'].split('_', 1)[1] == op for s in chosen), 3)
        for s in chosen:
            self.assertIs(s, next(x for x in samples if x['sample_id'] == s['sample_id']))

    def test_selection_is_independent_of_order_questions_answers_and_success(self):
        samples, audit = self.fixture()
        ids = lambda rows: [s['sample_id'] for s in rows]
        before = ids(seal.select(samples, audit, 'fixed', 3))
        for s in samples:
            s['questions'][0].update(answer='changed', score=0, question='changed')
            s['sessions'].append({'full': 'more difficult history'})
        self.assertEqual(ids(seal.select(list(reversed(samples)), audit, 'fixed', 3)), before)

    def test_invalid_or_exhausted_inventory_never_returns_a_partial_cohort(self):
        samples, audit = self.fixture()
        with self.assertRaisesRegex(ValueError, 'Insufficient'):
            seal.select(samples[:10], audit, 'fixed', 3)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            seal.select(samples + [samples[0]], audit, 'fixed', 3)
        audit['recorded_exposed_groups'].append('A02')
        with self.assertRaisesRegex(ValueError, 'overlap'):
            seal.select(samples, audit, 'fixed', 3)


if __name__ == '__main__':
    unittest.main()
