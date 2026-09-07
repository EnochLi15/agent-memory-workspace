import importlib.util
import unittest
from pathlib import Path
spec = importlib.util.spec_from_file_location('comparison', Path(__file__).parents[1]/'compare-criterion-audits.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ComparisonTests(unittest.TestCase):
    def test_unresolved_cases_remain_in_denominator_and_review(self):
        packet = {x: {'qid': x, 'evaluation_type': 'OperationTrace'} for x in ('a','b','c')}
        left = {x: {**p, 'status': 'judged', 'correct': True} for x,p in packet.items()}
        right = {x: dict(r) for x,r in left.items()}
        right['b']['correct'] = False
        right['c'].update(status='protocol_error', correct=None)
        summary, pending = m.compare(left, right, packet)
        self.assertEqual(summary['planned'], 3)
        self.assertEqual(sum(summary['joint_verdicts'].values()), 3)
        self.assertEqual([r['qid'] for r in pending], ['b','c'])
        with self.assertRaises(ValueError):
            m.compare(left, {x:r for x,r in right.items() if x != 'c'}, packet)
        right['a']['correct'] = 1
        with self.assertRaises(ValueError):
            m.compare(left, right, packet)


if __name__ == '__main__':
    unittest.main()
