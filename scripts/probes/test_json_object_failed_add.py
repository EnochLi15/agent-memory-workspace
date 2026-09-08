import copy,importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('json_object_wrapper',Path(__file__).with_name('run-json-object-failed-add.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class JsonObjectFailedAddTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.campaign=Path(self.tmp.name)/'fixture';self.campaign.mkdir()
        self.original={'verificationResponseFormat':'json_schema','llmModel':'glm-5.2','tokenBudget':6000,'failed_add_execution':{'scope':'fixture'}}
        self.environment={'KEPT':'same','SERVICE_CONFIG_JSON':'old','SERVICE_CONFIG_SHA256':'old'}
        self.env_patch=patch.object(m,'BASE_ENV',return_value=(self.environment,self.original));self.env_patch.start();self.addCleanup(self.env_patch.stop)
        inputs=self.campaign/'inputs.json';m.write(inputs,{'fixture':True})
        effective=self.campaign/'effective-service-config.json';_,config=m.execution_env({},{});m.write(effective,config)
        self.plan={'configuration_override':dict(m.OVERRIDE),'baseline_service_commit':m.COMMIT,'entry_point':str(m.HERE),'candidate_provenance':{'commit':m.COMMIT,'manifest_sha256':m.MANIFEST_SHA},'inputs_sha256':m.INPUT_SHA,'inputs_source':str(m.INPUTS),'inputs':str(inputs),'effective_service_config':str(effective),'effective_service_config_sha256':m.sha(effective.read_bytes()),'execution_files':{str(m.HERE):m.sha(m.HERE.read_bytes())}}

    def test_only_reviewed_setting_changes_and_environment_hash_is_recomputed(self):
        original=copy.deepcopy(self.original);env,config=m.execution_env({},self.plan)
        self.assertEqual(self.original,original);self.assertEqual(self.environment['SERVICE_CONFIG_JSON'],'old')
        expected=copy.deepcopy(original);expected.update(m.OVERRIDE);expected['failed_add_execution']['configuration_override']=m.OVERRIDE
        self.assertEqual(config,expected);self.assertEqual(env['KEPT'],'same')
        self.assertEqual(json.loads(env['SERVICE_CONFIG_JSON']),expected);self.assertEqual(env['SERVICE_CONFIG_SHA256'],m.sha(env['SERVICE_CONFIG_JSON'].encode()))

    def test_validate_rejects_other_override_and_rehashed_model_drift(self):
        self.assertEqual(m.check_configuration(self.campaign,self.plan)['verificationResponseFormat'],'json_object')
        with self.assertRaisesRegex(ValueError,'Only the reviewed'):m.check_configuration(self.campaign,{**self.plan,'configuration_override':{**m.OVERRIDE,'tokenBudget':12000}})
        effective=Path(self.plan['effective_service_config']);config=m.read(effective);config['llmModel']='different';m.write(effective,config)
        changed={**self.plan,'effective_service_config_sha256':m.sha(effective.read_bytes())}
        with self.assertRaisesRegex(ValueError,'Unreviewed effective configuration drift'):m.check_configuration(self.campaign,changed)

    def test_detach_always_starts_wrapper_entry_and_keeps_campaign(self):
        with patch.object(m,'validate') as validate,patch.object(m.fixed,'launch_detached',return_value={'pid':1}) as launch:
            self.assertEqual(m.launch(self.campaign),{'pid':1})
        validate.assert_called_once_with(self.campaign,require_idle=True)
        command=launch.call_args.args[0]
        self.assertEqual(command,[m.sys.executable,str(m.HERE),'--campaign',self.campaign.name,'--run'])
        self.assertNotIn(str(Path(m.fixed.__file__).resolve()),command)

    def test_actual_service_or_format_audit_drift_marks_summary_integrity_failed(self):
        _,expected=m.execution_env({},self.plan);m.write(self.campaign/'service.json',expected)
        valid={'kind':'generation','purpose':'source_erasure','verification_response_format':'json_object'}
        with patch.object(m,'BASE_SUMMARIZE',return_value={'integrity':'pass'}),patch.object(m.fixed.tail,'rows',return_value=[valid]):
            self.assertEqual(m.summarize(self.campaign,self.plan,{},[])['integrity'],'pass')
        with patch.object(m,'BASE_SUMMARIZE',return_value={'integrity':'pass'}),patch.object(m.fixed.tail,'rows',return_value=[{**valid,'verification_response_format':'json_schema'}]):
            result=m.summarize(self.campaign,self.plan,{},[]);self.assertEqual(result['integrity'],'fail');self.assertFalse(result['verification_format_audits_match'])
        m.write(self.campaign/'service.json',{**expected,'tokenBudget':12000})
        with patch.object(m,'BASE_SUMMARIZE',return_value={'integrity':'pass'}),patch.object(m.fixed.tail,'rows',return_value=[valid]):
            result=m.summarize(self.campaign,self.plan,{},[]);self.assertEqual(result['integrity'],'fail');self.assertFalse(result['effective_service_config_matches'])

if __name__=='__main__':unittest.main()
