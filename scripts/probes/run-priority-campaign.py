"""Run the frozen 972-question campaign once, with a MemOps ingestion gate.

The evaluator, service, datasets and spec are pinned before any model calls.
Only run-owned subprocesses are stopped, and no phase is resumed or rejudged.
"""
import argparse
from collections import Counter
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from experiment_runtime import ManagedRun, RunInterrupted, launch_detached, now, read_status, write_json
from judge_runtime import start_local_judge

PHASES = [('memops-risk', 'memops', 37, 7), ('memops-rest', 'memops', 435, 88), ('locomo', 'locomo', 500, 9)]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_rows(path, live=False):
    if not path.exists():
        return []
    result = []
    content = path.read_text()
    for index, line in enumerate(content.splitlines()):
        if not line.strip():
            continue
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            # A concurrent writer can have an unfinished last record.
            if not live or index != len(content.splitlines()) - 1 or content.endswith('\n'):
                raise
    return result


def expected_requests(samples, namespace):
    result = set()
    for sample in samples:
        for session in sample['sessions']:
            chunk, count, words = 0, 0, 0
            for message in session['messages']:
                size = len(re.split(r'\s+', message['content'].strip()))
                if count and (count >= 20 or words + size > 2000):
                    chunk += 1
                    count, words = 0, 0
                request = f"{namespace}:{sample['benchmark']}:{sample['sample_id']}:{session['session_id']}:{chunk}"
                result.add(request)
                count += 1
                words += size
    return result


def validate_partition(phases, datasets):
    if len(phases) != len(PHASES):
        raise ValueError('The campaign requires exactly three ordered phases')
    qids, sample_ids, run_ids, run_dirs = set(), set(), set(), set()
    for phase, samples, expected in zip(phases, datasets, PHASES):
        if (phase['id'], phase['benchmark'], phase['questions'], phase['samples']) != expected:
            raise ValueError('Phase order, benchmark or planned counts differ from the 972-question partition')
        if len(samples) != phase['samples']:
            raise ValueError('Dataset sample count differs from the frozen plan')
        count = 0
        for sample in samples:
            if sample['benchmark'] != phase['benchmark']:
                raise ValueError('Dataset benchmark differs from phase')
            sample_id = (sample['benchmark'], sample['sample_id'])
            if sample_id in sample_ids:
                raise ValueError('Duplicate sample across campaign phases')
            sample_ids.add(sample_id)
            sessions = [s['session_id'] for s in sample['sessions']]
            if len(sessions) != len(set(sessions)) or not sample['sessions'] or not sample['questions']:
                raise ValueError('Sample requires unique sessions and nonempty history/questions')
            for question in sample['questions']:
                if question['qid'] in qids:
                    raise ValueError('Duplicate qid across campaign phases')
                qids.add(question['qid'])
                count += 1
        if count != phase['questions']:
            raise ValueError('Dataset question count differs from the frozen plan')
        for key, known in [('run_id', run_ids), ('run_dir', run_dirs)]:
            value = str(Path(phase[key]).resolve()) if key == 'run_dir' else phase[key]
            if value in known:
                raise ValueError(f'Duplicate {key} across phases')
            known.add(value)
    if len(qids) != 972:
        raise ValueError('The campaign denominator must be 972')


def validate_plan(plan, campaign, fresh=True):
    if plan['protocol'] != 'priority-full-campaign-v1' or plan['campaign'] != campaign.name:
        raise ValueError('Campaign plan identity mismatch')
    if plan['planned_questions'] != 972 or plan.get('profile', 'candidate') != 'candidate':
        raise ValueError('The frozen campaign requires candidate profile and denominator 972')
    code = Path(plan['code_root'])
    if not code.is_absolute() or code.resolve() == ROOT:
        raise ValueError('A separate absolute frozen code_root is required')
    hashes = plan['frozen_code_hashes']
    if not hashes:
        raise ValueError('Frozen code hashes are required')
    for relative, expected in hashes.items():
        path = code / relative
        if Path(relative).is_absolute() or '..' in Path(relative).parts:
            raise ValueError('Frozen artifact paths must remain inside code_root')
        if sha(path.read_bytes()) != expected:
            raise ValueError(f'Frozen code hash mismatch: {relative}')
    for required in ('service/dist/server.js', 'service/dist/config.js', 'eval/dist/cli.js',
                     'eval/python/judge_bridge.py', 'eval/scripts/ollama-judge-server.py', 'eval/contracts/contract.json'):
        if required not in hashes:
            raise ValueError(f'Missing frozen code hash: {required}')
    spec_path = Path(plan['spec'])
    if not spec_path.is_absolute():
        raise ValueError('Spec path must be absolute')
    spec_bytes = spec_path.read_bytes()
    spec_expected = plan.get('spec_sha256')
    if spec_expected is None:
        try:
            spec_expected = hashes[str(spec_path.relative_to(code))]
        except (ValueError, KeyError):
            raise ValueError('The spec must have a pinned hash')
    if sha(spec_bytes) != spec_expected:
        raise ValueError('Frozen spec hash mismatch')
    if plan.get('selection_manifest'):
        if sha(Path(plan['selection_manifest']).read_bytes()) != plan['selection_manifest_sha256']:
            raise ValueError('Frozen selection manifest hash mismatch')
    spec = json.loads(spec_bytes)
    evaluation = spec['evaluation']
    if evaluation['concurrency'] != 1 or evaluation['benchmark_execution'] != 'sequential':
        raise ValueError('The frozen campaign requires sequential single-worker evaluation')
    if not evaluation.get('answer_model') or not evaluation.get('judge_model'):
        raise ValueError('Answer and rubric Judge models must be explicit')
    datasets = []
    for phase in plan['phases']:
        data_path, run_dir = Path(phase['data_file']), Path(phase['run_dir'])
        if not data_path.is_absolute() or not run_dir.is_absolute():
            raise ValueError('Dataset and evaluator output paths must be absolute')
        raw = data_path.read_bytes()
        if sha(raw) != phase['dataset_sha256']:
            raise ValueError(f'Dataset hash mismatch: {phase["id"]}')
        datasets.append(json.loads(raw))
        if fresh and run_dir.exists():
            raise FileExistsError(f'Preserve the existing phase output: {run_dir}')
    validate_partition(plan['phases'], datasets)
    if not Path(plan['data_dir']).is_absolute() or not plan['namespace']:
        raise ValueError('An absolute isolated data_dir and namespace are required')
    if fresh and Path(plan['data_dir']).exists():
        raise FileExistsError('Preserve existing service storage; this campaign cannot reuse ingestion')
    if type(plan['port']) is not int or not 1024 <= plan['port'] <= 65535:
        raise ValueError('Invalid dedicated service port')
    return spec, datasets


def ingestion_gate(manifest, ingest, judgments, phase, samples, namespace):
    """Separate ingestion integrity from answer/search/Judge quality failures."""
    reasons = []
    qids = {q['qid'] for sample in samples for q in sample['questions']}
    observed = [row['qid'] for row in judgments]
    expected = expected_requests(samples, namespace)
    latest = {row['request_id']: row for row in ingest}
    failed = [key for key, row in latest.items() if row.get('status') != 'ok']
    if manifest.get('status') != 'finished':
        reasons.append('manifest_not_finished')
    for key, expected_value in [('run_id', phase['run_id']), ('memory_namespace', namespace),
                                ('planned_questions', phase['questions']), ('dataset_sha256', phase['dataset_sha256'])]:
        if manifest.get(key) != expected_value:
            reasons.append('manifest_mismatch:' + key)
    if manifest.get('samples') != [sample['sample_id'] for sample in samples]:
        reasons.append('manifest_sample_mismatch')
    if set(observed) != qids or len(observed) != len(qids):
        reasons.append('judgment_set_incomplete_or_duplicated')
    if failed:
        reasons.append('final_ingest_non_ok')
    missing, unexpected = sorted(expected - latest.keys()), sorted(latest.keys() - expected)
    if missing:
        reasons.append('missing_ingest_receipts')
    if unexpected:
        reasons.append('unexpected_ingest_receipts')
    service_errors = sorted({row['sample_id'] for row in judgments if row.get('status') == 'service_error'})
    if service_errors:
        reasons.append('service_error_samples')
    return {'protocol': 'priority-ingestion-gate-v1', 'phase': phase['id'],
            'status': 'fail' if reasons else 'pass', 'checked_at': now(), 'reasons': reasons,
            'expected_adds': len(expected), 'successful_adds': sum(row.get('status') == 'ok' for row in latest.values()),
            'final_non_ok_requests': sorted(failed), 'missing_requests': missing, 'unexpected_requests': unexpected,
            'service_error_samples': service_errors,
            'question_status_counts': dict(Counter(row.get('status') for row in judgments)),
            'scope': 'Answer, search pipeline and Judge errors retain zero credit without failing ingestion.'}


def phase_metrics(phase, service_rows=()):
    directory = Path(phase['run_dir'])
    ingest = read_rows(directory / 'ingest.jsonl', live=True)
    judgments = read_rows(directory / 'judgments.jsonl', live=True)
    latest = {row['request_id']: row for row in ingest}
    # Duplicates are never silently picked as a better score.
    unique = {row['qid']: row for row in judgments}
    duplicate_qids = len(judgments) != len(unique)
    correct = sum(row.get('status') == 'judged' and row.get('correct') is True for row in unique.values())
    degraded = {r.get('request_id') for r in service_rows if r.get('event') == 'degraded' and r.get('request_id') in latest}
    return {'questions': phase['questions'], 'successful_adds': sum(r.get('status') == 'ok' for r in latest.values()),
            'final_non_ok_adds': sum(r.get('status') != 'ok' for r in latest.values()), 'degraded_adds': len(degraded),
            'recorded_questions': len(unique), 'judged': sum(r.get('status') == 'judged' for r in unique.values()),
            'correct': correct, 'accuracy_over_planned': correct / phase['questions'],
            'question_status_counts': dict(Counter(r.get('status') for r in unique.values())),
            'duplicate_judgments': duplicate_qids}


def service_events(campaign):
    result = []
    path = campaign / 'service.log'
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
                if isinstance(row, dict) and row.get('event') == 'degraded':
                    result.append(row)
            except json.JSONDecodeError:
                pass  # Server logs may contain non-JSON startup diagnostics.
    return result


def summarize(plan, campaign, statuses):
    events = service_events(campaign)
    phases = [{**phase_metrics(p, events), 'id': p['id'], 'status': statuses.get(p['id'], 'pending')}
              for p in plan['phases']]
    correct = sum(p['correct'] for p in phases)
    return {'protocol': 'priority-campaign-status-v1', 'campaign': plan['campaign'], 'updated_at': now(),
            'planned_questions': 972, 'correct': correct, 'accuracy_over_planned': correct / 972,
            'recorded_questions': sum(p['recorded_questions'] for p in phases), 'phases': phases,
            'scope': 'Fixed planned denominator; all errors and unrecorded questions receive zero credit.'}


def private_log(files, path):
    stream = files.enter_context(path.open('x'))
    os.chmod(path, 0o600)
    return stream


def run_campaign(plan, campaign, spec, datasets):
    code = Path(plan['code_root'])
    runtime_path = campaign / 'runtime.json'
    statuses = {}
    env = os.environ.copy()
    for line in Path(plan.get('env_file', ROOT / '.env')).read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    env.update(spec['defaults'])
    env.update(spec['profiles']['candidate']['environment'])
    env.update(PORT=str(plan['port']), HOST='127.0.0.1', MEMORY_DATA_DIR=plan['data_dir'],
               MEMORY_MODEL_AUDIT=str(campaign / 'model-calls.jsonl'),
               MEMORY_MODEL_TRACE=str(campaign / 'model-trace.jsonl'),
               MEMORY_RETRIEVAL_AUDIT=str(campaign / 'retrieval-stages.jsonl'))
    evaluation = spec['evaluation']
    # Claim storage before opening sockets; an occupied port never attaches to another run.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', plan['port']))
    try:
        with ExitStack() as files, ManagedRun(runtime_path) as runtime:
            Path(plan['data_dir']).mkdir(parents=True)
            safe = json.loads(subprocess.check_output(['node', '--input-type=module', '-e',
                "import {configFromEnv} from './dist/config.js';const {llmKey,...safe}=configFromEnv();console.log(JSON.stringify(safe));"],
                cwd=code / 'service', env=env, text=True))
            safe.update(experiment_spec_sha256=sha(Path(plan['spec']).read_bytes()),
                        evaluation_configuration=evaluation, private_model_trace_enabled=True,
                        campaign_plan_sha256=sha((campaign / 'plan.json').read_bytes()),
                        source_checkpoints=plan['source_checkpoints'], frozen_code_hashes=plan['frozen_code_hashes'],
                        selection_manifest_sha256=plan['selection_manifest_sha256'])
            safe['extraction_prompt_sha256'] = plan['frozen_code_hashes'].get('service/src/prompts.ts')
            env['SERVICE_CONFIG_JSON'] = json.dumps(safe, sort_keys=True, separators=(',', ':'))
            env['SERVICE_CONFIG_SHA256'] = sha(env['SERVICE_CONFIG_JSON'].encode())
            write_json(campaign / 'service.json', safe)
            write_json(campaign / 'phase-status.json', summarize(plan, campaign, statuses))
            if sys.platform == 'darwin':
                runtime.spawn('sleep-prevention', ['/usr/bin/caffeinate', '-i', '-w', str(os.getpid())],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            service = runtime.spawn('service', ['node', str(code / 'service/dist/server.js')], required=True,
                                    cwd=code / 'service', env=env, stdin=subprocess.DEVNULL,
                                    stdout=private_log(files, campaign / 'service.log'), stderr=subprocess.STDOUT)
            base = f'http://127.0.0.1:{plan["port"]}'
            for _ in range(80):
                runtime.poll()
                try:
                    with urllib.request.urlopen(base + '/health', timeout=1) as response:
                        if response.status == 200:
                            break
                except Exception:
                    time.sleep(.25)
            else:
                raise RuntimeError('Owned service did not become ready')
            for index, (phase, samples) in enumerate(zip(plan['phases'], datasets)):
                statuses[phase['id']] = 'preflight'
                write_json(campaign / 'phase-status.json', summarize(plan, campaign, statuses))
                run_env = {**env, 'EVAL_PYTHON': str(code / 'eval/.venv/bin/python')}
                for key in ('EVALUATOR_API_BASE', 'EVALUATOR_API_KEY', 'EVALUATOR_MODEL'):
                    run_env.pop(key, None)
                locomo = phase['benchmark'] == 'locomo'
                if locomo:
                    judge_base = start_local_judge(runtime, files, code, campaign, 'locomo', env)
                    run_env.update(EVALUATOR_API_BASE=judge_base, EVALUATOR_API_KEY='local')
                command = ['node', str(code / 'eval/dist/cli.js'), 'run', '--data', phase['data_file'],
                           '--output', phase['run_dir'], '--run-id', phase['run_id'], '--memory-namespace', plan['namespace'],
                           '--base-url', base, '--concurrency', str(evaluation['concurrency']),
                           '--answer-model', evaluation['answer_model'],
                           '--judge-model', 'qwen3:14b' if locomo else evaluation['judge_model'],
                           '--judge-kind', 'refined-python' if locomo else 'rubric',
                           '--mode', 'upstream-reproduction' if locomo else 'proxy',
                           '--chunk-messages', '20', '--chunk-words', '2000']
                output = private_log(files, campaign / (phase['id'] + '.log'))
                job = runtime.spawn(phase['id'], command, cwd=code / 'eval', env=run_env,
                                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
                statuses[phase['id']] = 'running'
                print(json.dumps({'event': 'phase_started', 'phase': phase['id'], 'pid': job.pid, 'questions': phase['questions']}), flush=True)
                last_status = 0
                while job.poll() is None:
                    runtime.poll()
                    if time.monotonic() - last_status > 5:
                        write_json(campaign / 'phase-status.json', summarize(plan, campaign, statuses))
                        last_status = time.monotonic()
                    time.sleep(.2)
                runtime.poll()
                output.close()
                directory = Path(phase['run_dir'])
                if job.returncode:
                    statuses[phase['id']] = 'failed'
                    if index == 0:
                        write_json(campaign / 'gate.json', {'status': 'fail', 'phase': phase['id'], 'checked_at': now(),
                                                          'reasons': ['evaluator_process_failed'], 'exit_code': job.returncode})
                    raise RuntimeError(f'Evaluator failed in {phase["id"]}; no subsequent phase was launched')
                verdict = ingestion_gate(json.loads((directory / 'manifest.json').read_text()),
                                         read_rows(directory / 'ingest.jsonl'), read_rows(directory / 'judgments.jsonl'),
                                         phase, samples, plan['namespace'])
                write_json(campaign / (phase['id'] + '-integrity.json'), verdict)
                statuses[phase['id']] = 'finished'
                if index == 0:
                    write_json(campaign / 'gate.json', verdict)
                    if verdict['status'] != 'pass':
                        statuses[phase['id']] = 'gate_failed'
                        raise RuntimeError('High-risk ingestion gate failed; remaining 935 questions were not launched')
                elif any(r.startswith('manifest_') or r == 'judgment_set_incomplete_or_duplicated' for r in verdict['reasons']):
                    statuses[phase['id']] = 'failed'
                    raise RuntimeError(f'Incomplete or mismatched phase artifacts: {phase["id"]}')
                write_json(campaign / 'phase-status.json', summarize(plan, campaign, statuses))
                print(json.dumps({'event': 'phase_finished', 'phase': phase['id'], 'integrity': verdict['status']}), flush=True)
            summary = summarize(plan, campaign, statuses)
            summary['status'] = 'finished'
            write_json(campaign / 'summary.json', summary)
    except BaseException:
        for phase_id, status in list(statuses.items()):
            if status in ('running', 'preflight'):
                statuses[phase_id] = 'failed'
        raise
    finally:
        write_json(campaign / 'phase-status.json', summarize(plan, campaign, statuses))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', required=True)
    parser.add_argument('--detach', action='store_true')
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', args.campaign):
        parser.error('Campaign must be a simple directory name')
    campaign = ROOT / 'artifacts' / args.campaign
    plan = json.loads((campaign / 'plan.json').read_text())
    runtime_path = campaign / 'runtime.json'
    if args.status:
        runtime = read_status(runtime_path) if runtime_path.exists() or runtime_path.with_suffix('.launch.json').exists() else {'status': 'not_started', 'alive': False}
        saved = json.loads((campaign / 'phase-status.json').read_text()) if (campaign / 'phase-status.json').exists() else {'phases': []}
        status = summarize(plan, campaign, {p['id']: p['status'] for p in saved['phases']})
        status['runtime'] = {'status': runtime['status'], 'alive': runtime['alive'], 'pid': runtime.get('pid'), 'error_type': runtime.get('error_type')}
        if (campaign / 'gate.json').exists():
            status['gate'] = json.loads((campaign / 'gate.json').read_text())
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return
    os.umask(0o077)
    spec, datasets = validate_plan(plan, campaign)
    if args.detach:
        command = [sys.executable, str(Path(__file__).resolve()), '--campaign', args.campaign]
        print(json.dumps(launch_detached(command, runtime_path, cwd=ROOT), indent=2))
        return
    run_campaign(plan, campaign, spec, datasets)


if __name__ == '__main__':
    try:
        main()
    except RunInterrupted as error:
        sys.exit(128 + error.signum)
