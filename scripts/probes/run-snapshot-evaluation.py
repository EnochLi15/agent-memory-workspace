"""Expand questions on a frozen history using cloned storage and normal HTTP receipts.

This diagnostic does not relax the formal runner's clean paired-ablation guard.
The evaluator still sees only HTTP. Original artifacts and storage are preserved.
"""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from experiment_runtime import ManagedRun, launch_detached, run_benchmark_jobs
from judge_runtime import start_local_judge


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def validate_history(original, expanded):
    before = {sample['sample_id']: sample for sample in original}
    if len(before) != len(original) or len(expanded) != len(original):
        raise ValueError('Snapshot evaluation requires the same unique samples')
    if {s['sample_id'] for s in expanded} != set(before):
        raise ValueError('Snapshot sample identities differ')
    for sample in expanded:
        prior = before[sample['sample_id']]
        for key in ('benchmark', 'group_id', 'sessions'):
            if prior[key] != sample[key]:
                raise ValueError(f'Snapshot history differs: {sample["sample_id"]}/{key}')
        if not all(q in sample['questions'] for q in prior['questions']):
            raise ValueError('Expanded dataset must retain the original questions unchanged')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', required=True)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--origin-data', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--benchmark', choices=['locomo', 'memops'], default='locomo')
    parser.add_argument('--candidate-dist', type=Path, help='Explicit frozen compiled candidate with a sibling code-snapshot.json; records a read-only candidate experiment')
    parser.add_argument('--detach', action='store_true')
    args = parser.parse_args()
    campaign = ROOT / 'artifacts' / args.campaign
    runtime_file = campaign / 'candidate-runtime.json'
    if args.detach:
        command = [sys.executable, str(Path(__file__).resolve())] + [a for a in sys.argv[1:] if a != '--detach']
        print(json.dumps(launch_detached(command, runtime_file, cwd=ROOT), indent=2))
        return
    origin = ROOT / 'artifacts' / args.origin
    origin_eval = ROOT / 'eval/artifacts' / (args.origin + '-candidate-' + args.benchmark)
    prior = json.loads((origin_eval / 'manifest.json').read_text())
    if prior['status'] != 'finished' or json.loads((origin / 'candidate-runtime.json').read_text())['status'] != 'finished':
        raise ValueError('Origin must have finished and released its storage')
    if sha(args.origin_data.read_bytes()) != prior['dataset_sha256']:
        raise ValueError('Origin data hash mismatch')
    original = json.loads(args.origin_data.read_text())
    expanded = json.loads(args.data.read_text())
    validate_history(original, expanded)
    if any(s['benchmark'] != args.benchmark for s in expanded):
        raise ValueError('Dataset benchmark differs from the requested benchmark')
    latest = {row['request_id']: row for row in rows(origin_eval / 'ingest.jsonl')}
    if not latest or any(r['status'] != 'ok' for r in latest.values()):
        raise ValueError('Origin contains incomplete ingestion')
    source = json.loads((origin / 'source-checkpoint.json').read_text())
    candidate = None
    if args.candidate_dist:
        args.candidate_dist = args.candidate_dist.resolve()
        candidate = json.loads((args.candidate_dist.parent / 'code-snapshot.json').read_text())
        actual = {str(p.relative_to(args.candidate_dist)):sha(p.read_bytes()) for p in args.candidate_dist.rglob('*.js')}
        if actual != candidate['dist_hashes']:
            raise ValueError('Frozen candidate artifact hash mismatch')
    else:
        for part in ('service', 'eval'):
            commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT / part, text=True).strip()
            patch = sha(subprocess.check_output(['git', 'diff', 'HEAD'], cwd=ROOT / part))
            if commit != source[part]['commit'] or patch != source[part]['tracked_diff_sha256']:
                raise ValueError(f'{part} changed since the frozen ingestion')
    env = os.environ.copy()
    for line in (ROOT / '.env').read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    safe = json.loads((origin / 'candidate-service.json').read_text())
    original_store = Path(safe['dataDir'])
    campaign.mkdir(parents=True, exist_ok=True)
    cloned_store = campaign / 'state'
    cloned_store.mkdir()
    snapshot = []
    actual_receipts = set()
    for database in sorted(original_store.glob('*/memory.sqlite')):
        target = cloned_store / database.relative_to(original_store)
        target.parent.mkdir()
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(target) as dest:
            src.backup(dest)
            actual_receipts.update(r[0] for r in dest.execute('SELECT id FROM requests'))
        snapshot.append({'path':str(target.relative_to(cloned_store)), 'sha256':sha(target.read_bytes())})
    if actual_receipts != set(latest):
        raise ValueError('Snapshot receipt set differs from the completed ingestion')
    safe.pop('locomo_input_override', None)
    safe.pop('memops_input_override', None)
    safe[f'{args.benchmark}_input_override'] = str(args.data.resolve())
    safe.update(dataDir=str(cloned_store), port=args.port,
                ingestion_origin={'run_id':prior['run_id'], 'manifest_sha256':sha((origin_eval / 'manifest.json').read_bytes()),
                                  'kind':'frozen-history-question-expansion', 'receipts':len(latest), 'snapshot':snapshot})
    safe.pop('llmKey', None)
    if candidate:
        safe['candidate_execution_artifact'] = {'path':str(args.candidate_dist), **candidate}
        safe['experiment_scope'] = 'Candidate retrieval on frozen ingestion; current checkout metadata may differ from the executed, hash-pinned compiled artifact'
    (campaign / 'candidate-service.json').write_text(json.dumps(safe, indent=2) + '\n')
    (campaign / 'source-checkpoint.json').write_text(json.dumps(source, indent=2) + '\n')
    env.update(MEMORY_MODEL_AUDIT=str(campaign / 'candidate-model-calls.jsonl'),
               MEMORY_RETRIEVAL_AUDIT=str(campaign / 'retrieval-stages.jsonl'))
    env.pop('MEMORY_MODEL_TRACE', None)
    env['SERVICE_CONFIG_JSON'] = json.dumps(safe, sort_keys=True, separators=(',', ':'))
    env['SERVICE_CONFIG_SHA256'] = sha(env['SERVICE_CONFIG_JSON'].encode())
    with ExitStack() as files, ManagedRun(runtime_file) as runtime:
        if sys.platform == 'darwin':
            runtime.spawn('sleep-prevention', ['/usr/bin/caffeinate', '-i', '-w', str(os.getpid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        judge_base = start_local_judge(runtime, files, ROOT, campaign, 'candidate', env) if args.benchmark == 'locomo' else safe['llmBase']
        log = files.enter_context(open(campaign / 'candidate-service.log', 'x'))
        server_module = (args.candidate_dist or ROOT / 'service/dist') / 'server.js'
        launcher = "import {buildServer} from " + json.dumps(server_module.as_uri()) + ";const c=JSON.parse(process.env.SERVICE_CONFIG_JSON);c.llmKey=process.env.MEMORY_LLM_API_KEY;const app=await buildServer(c);await app.listen({host:'127.0.0.1',port:c.port});for(const s of ['SIGINT','SIGTERM'])process.once(s,()=>{void app.close()});"
        launcher_file = campaign / 'snapshot-service.mjs'
        launcher_file.write_text(launcher + '\n')
        service = runtime.spawn('service', ['node', str(launcher_file)], required=True, cwd=ROOT / 'service', env=env, stdout=log, stderr=subprocess.STDOUT)
        for _ in range(80):
            runtime.poll()
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{args.port}/health', timeout=1) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(.25)
        else:
            raise RuntimeError('Cloned service did not become ready')
        def launch(benchmark):
            run_id = args.campaign + '-candidate-' + benchmark
            run_env = {**env, 'EVAL_PYTHON':str(ROOT / 'eval/.venv/bin/python'),
                       'MEMORY_LLM_BASE_URL':safe['llmBase'], 'EVALUATOR_API_BASE':judge_base, 'EVALUATOR_API_KEY':'local' if benchmark == 'locomo' else env.get('MEMORY_LLM_API_KEY', '')}
            command = ['node', 'dist/cli.js', 'run', '--data', str(args.data.resolve()), '--run-id', run_id,
                       '--memory-namespace', prior['memory_namespace'], '--base-url', f'http://127.0.0.1:{args.port}',
                       '--concurrency', '1', '--answer-model', prior['answer_model'], '--judge-model', prior['judge_model'],
                       '--judge-kind', prior['judge_kind'], '--mode', prior['mode']]
            out = files.enter_context(open(campaign / ('candidate-' + benchmark + '.log'), 'x'))
            job = runtime.spawn(benchmark, command, cwd=ROOT / 'eval', env=run_env, stdout=out, stderr=subprocess.STDOUT)
            print(json.dumps({'event':'started', 'run_id':run_id, 'questions':sum(len(s['questions']) for s in expanded), 'pid':job.pid}), flush=True)
            return benchmark, job, out
        run_benchmark_jobs(runtime, service, [args.benchmark], launch, 'sequential')
    audit = campaign / 'candidate-model-calls.jsonl'
    calls = rows(audit) if audit.exists() else []
    writes = [r for r in calls if r.get('kind') == 'generation']
    if writes:
        raise RuntimeError('Snapshot receipt replay unexpectedly called a write model')
    (campaign / 'snapshot-verification.json').write_text(json.dumps({'write_model_calls':0, 'http_receipts_expected':len(latest), 'original_storage_preserved':True}, indent=2) + '\n')


if __name__ == '__main__':
    main()
