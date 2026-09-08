"""One fixed-baseline HTTP add using the existing json_object configuration."""
import argparse, copy, importlib.util, json, os, sys
from pathlib import Path
HERE=Path(__file__).resolve();ROOT=HERE.parents[2]
spec=importlib.util.spec_from_file_location('json_object_fixed',HERE.with_name('run-fixed-failed-adds.py'));fixed=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixed)
BASE_PREPARE,BASE_VALIDATE,BASE_ENV,BASE_SUMMARIZE=fixed.prepare,fixed.validate,fixed.execution_env,fixed.summarize
read,write,sha=fixed.read_json,fixed.write_json,fixed.sha
COMMIT='5ca2929d09e718c4aa5353460a1f9338ad0a9c30'
MANIFEST_SHA='fd1590e3e523dd316a1f2fe87d90c454ca0d1c310467ff3aa4686cd7bd3b5d95'
BASELINE=ROOT/'artifacts/priority-deferral-scope-split-20260909'
INPUTS=ROOT/'artifacts/priority-B03-segment45-add-20260909-01/inputs.private.json'
INPUT_SHA='e3c82da5a775808b9e19049b5cd31fce0a9e61aa0979b138fe5a3d1b87734ff3'
OVERRIDE={'verificationResponseFormat':'json_object'}
FORMAT_PURPOSES={'verification','erasure_binding','source_erasure','source_erasure_repair','state_transition'}


def execution_env(data,plan):
    env,original=BASE_ENV(data,plan);config=copy.deepcopy(original);config.update(OVERRIDE)
    config['failed_add_execution']['configuration_override']=dict(OVERRIDE)
    env=dict(env);env['SERVICE_CONFIG_JSON']=json.dumps(config,sort_keys=True,separators=(',',':'))
    env['SERVICE_CONFIG_SHA256']=sha(env['SERVICE_CONFIG_JSON'].encode())
    return env,config


def check_configuration(campaign,plan):
    expected_file=campaign/'effective-service-config.json'
    if plan.get('configuration_override')!=OVERRIDE or plan.get('baseline_service_commit')!=COMMIT or plan.get('entry_point')!=str(HERE):raise ValueError('Only the reviewed json_object configuration override is allowed')
    if plan['candidate_provenance'].get('commit')!=COMMIT or plan['candidate_provenance'].get('manifest_sha256')!=MANIFEST_SHA:raise ValueError('The fixed unified baseline changed')
    if plan['inputs_sha256']!=INPUT_SHA or Path(plan['inputs_source']).resolve()!=INPUTS:raise ValueError('The unchanged segment45 inputs are required')
    if plan.get('effective_service_config')!=str(expected_file) or plan['execution_files'].get(str(HERE))!=sha(HERE.read_bytes()):raise ValueError('Wrapper entry or effective configuration pin changed')
    if sha(expected_file.read_bytes())!=plan.get('effective_service_config_sha256'):raise ValueError('Frozen effective configuration changed')
    _,config=execution_env(read(plan['inputs']),plan)
    if read(expected_file)!=config:raise ValueError('Unreviewed effective configuration drift')
    return config


def validate(campaign,require_idle=False,allow_owned_launch=False):
    result=BASE_VALIDATE(campaign,require_idle,allow_owned_launch);plan=read(campaign/'plan.json');check_configuration(campaign,plan)
    return {**result,'configuration_override':dict(OVERRIDE),'entry_point':str(HERE),'effective_service_config_sha256':plan['effective_service_config_sha256']}


def prepare(args):
    if args.expected_service_commit!=COMMIT or args.inputs.resolve()!=INPUTS or args.inputs_sha256!=INPUT_SHA or sha(args.candidate_manifest.read_bytes())!=MANIFEST_SHA:raise ValueError('Use the fixed unified baseline and unchanged segment45 inputs')
    BASE_PREPARE(args);campaign=ROOT/'artifacts'/fixed.tail.name(args.campaign);plan=read(campaign/'plan.json')
    _,config=execution_env(read(plan['inputs']),plan);effective=campaign/'effective-service-config.json';write(effective,config)
    plan.update(configuration_override=dict(OVERRIDE),baseline_service_commit=COMMIT,entry_point=str(HERE),effective_service_config=str(effective),effective_service_config_sha256=sha(effective.read_bytes()))
    plan['execution_files'][str(HERE)]=sha(HERE.read_bytes());plan['execution_files'][str(effective)]=sha(effective.read_bytes());write(campaign/'plan.json',plan)
    result=validate(campaign);write(campaign/'validation.json',result);return result


def summarize(campaign,plan,data,results):
    summary=BASE_SUMMARIZE(campaign,plan,data,results)
    try:
        expected=check_configuration(campaign,plan)
        actual_ok=read(campaign/'service.json')==expected
        audits=[r for r in fixed.tail.rows(campaign/'model-calls.jsonl') if r.get('kind')=='generation' and r.get('purpose') in FORMAT_PURPOSES]
        audit_ok=all(r.get('verification_response_format')=='json_object' for r in audits)
    except (OSError,ValueError,KeyError):actual_ok=audit_ok=False;audits=[]
    summary.update(configuration_override=dict(OVERRIDE),effective_service_config_matches=actual_ok,verification_format_audits_match=audit_ok,verification_format_audit_records=len(audits))
    if not actual_ok or not audit_ok:summary['integrity']='fail'
    write(campaign/'summary.json',summary);return summary


def launch(campaign):
    validate(campaign,require_idle=True)
    return fixed.launch_detached([sys.executable,str(HERE),'--campaign',campaign.name,'--run'],campaign/'runtime.json',cwd=ROOT)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--campaign',default='priority-B03-segment45-add-20260909-02')
    actions=parser.add_mutually_exclusive_group(required=True)
    for action in ['plan','validate','run']:actions.add_argument('--'+action,action='store_true')
    parser.add_argument('--inputs',type=Path,default=INPUTS);parser.add_argument('--inputs-sha256',default=INPUT_SHA)
    parser.add_argument('--candidate-dist',type=Path,default=BASELINE/'final-dist');parser.add_argument('--candidate-manifest',type=Path,default=BASELINE/'final-manifest.json');parser.add_argument('--expected-service-commit',default=COMMIT)
    parser.add_argument('--port',type=int,default=8124);parser.add_argument('--detach',action='store_true');args=parser.parse_args();os.umask(0o077)
    if args.detach and not args.run:parser.error('--detach requires --run')
    campaign=ROOT/'artifacts'/fixed.tail.name(args.campaign)
    if args.plan:print(json.dumps(prepare(args),indent=2));return
    if args.validate:print(json.dumps(validate(campaign),indent=2));return
    fixed.validate=validate;fixed.execution_env=execution_env;fixed.summarize=summarize
    if args.detach:print(json.dumps(launch(campaign),indent=2))
    else:fixed.execute(campaign)


if __name__=='__main__':
    try:main()
    except fixed.RunInterrupted as error:sys.exit(128+error.signum)
