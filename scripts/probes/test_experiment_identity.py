import copy
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from experiment_identity import validate_reuse


class IngestionIdentityTests(unittest.TestCase):
    def setUp(self):
        self.config = {'writeContinuation': False, 'dataDir': '/same', 'port': 8096, 'llmModel': 'mini',
                       'llmStageModels': {'verification': 'strong'}, 'sourceIndex': True,
                       'embeddingDigest': 'pinned', 'maxRepairRounds': 2, 'extractionWorkers': 1, 'sourceErasureWorkers': 1, 'sourceErasureGrouped': False,
                       'rawFallback': False, 'rerank': False,
                       'experimental': {'multiHop': False, 'lifecycle': True},
                       'evaluation_configuration': {'answer_model': 'mini'}}
        self.identities = {n: {'commit': n + '-commit', 'dirty': False} for n in ('service', 'eval')}
        self.prior = {'status': 'finished', 'dataset_sha256': 'dataset',
                      'service_commit': 'service-commit', 'eval_commit': 'eval-commit',
                      'source_state': {n: {'dirty': False} for n in ('service', 'eval')},
                      'service_configuration': copy.deepcopy(self.config)}

    def check(self, current=None):
        validate_reuse(self.prior, current or self.config, 'dataset', self.identities)

    def test_pure_retrieval_changes_keep_the_same_ingestion(self):
        changed = copy.deepcopy(self.config)
        changed.update(port=8097, rawFallback=True, rerank=True,
                       relationMode='conditional', rerankPolicy='selective', rerankFormat='indices')
        changed['experimental']['multiHop'] = True
        self.check(changed)
        self.assertFalse(self.config['experimental']['multiHop'])

    def test_model_representation_lifecycle_and_evaluation_changes_are_rejected(self):
        for field, value in [('writeContinuation', True), ('llmModel', 'new'), ('sourceIndex', False),
                             ('embeddingDigest', 'other'), ('maxRepairRounds', 1), ('extractionWorkers', 3), ('sourceErasureWorkers', 3), ('sourceErasureGrouped', True),
                             ('llmStageModels', {'verification': 'other'}),
                             ('experimental', {'multiHop': False, 'lifecycle': False}),
                             ('evaluation_configuration', {'answer_model': 'other'})]:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'configuration changed'):
                self.check({**self.config, field: value})

    def test_dataset_and_unfinished_run_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'different dataset'):
            validate_reuse(self.prior, self.config, 'other', self.identities)
        self.prior['status'] = 'running'
        with self.assertRaisesRegex(ValueError, 'not finished'):
            self.check()

    def test_changed_or_unrecorded_source_cannot_claim_a_paired_run(self):
        for name in ('service', 'eval'):
            self.identities[name]['commit'] += '-new'
            with self.assertRaisesRegex(ValueError, 'commit'):
                self.check()
            self.identities[name]['commit'] = name + '-commit'
            self.identities[name]['dirty'] = True
            with self.assertRaisesRegex(ValueError, 'clean'):
                self.check()
            self.identities[name]['dirty'] = False
        del self.prior['source_state']['service']['dirty']
        with self.assertRaisesRegex(ValueError, 'clean'):
            self.check()


if __name__ == '__main__':
    unittest.main()
