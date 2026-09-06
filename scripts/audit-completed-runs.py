"""Audit complete campaign artifacts without calling a model or changing scores."""
import argparse
import collections
import datetime
import hashlib
import json
import pathlib
import statistics
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--require-all', action='store_true')
parser.add_argument('--run-id', action='append', help='Audit only these explicit completed runs')
parser.add_argument('--output', type=pathlib.Path, help='Separate output required for explicit runs')
args = parser.parse_args()
profiles = ['U3', 'B2', 'B0', 'B1', 'U2', 'B3', 'B4', 'B5', 'B6',
            'no_lifecycle', 'no_raw', 'no_time', 'no_hop', 'no_rerank']
expected = [f'dev-v6-{p}-{b}' for p in profiles for b in ['locomo', 'memops']]
expected += [f'baseline-v7-{p}-{b}' for p in ['U0', 'U1'] for b in ['locomo', 'memops']]
expected += [f'holdout-v2-U3-{b}' for b in ['locomo', 'memops']]
if args.run_id:
    if not args.output:
        parser.error('--run-id requires --output to preserve historical audits')
    if len(set(args.run_id)) != len(args.run_id) or any(not r or '/' in r or '\\' in r or r in ('.', '..') for r in args.run_id):
        parser.error('Expected unique run directory names')
    expected = args.run_id


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def distribution(values):
    values = sorted(values)
    if not values:
        return {'n': 0}
    return {'n': len(values), 'mean': statistics.mean(values), **{
        label: values[min(len(values) - 1, int(len(values) * q))]
        for label, q in [('p50', .5), ('p95', .95), ('p99', .99), ('max', 1)]}}


datasets = {sha(p): p for p in sorted((ROOT / 'eval/.data').glob('*-*.json'))
            if not p.name.endswith('.manifest.json')}
finished, pending = [], []
for run_id in expected:
    directory = ROOT / 'eval/artifacts' / run_id
    path = directory / 'manifest.json'
    manifest = json.loads(path.read_text()) if path.exists() else {}
    if manifest.get('status') == 'finished':
        finished.append((directory, manifest))
    else:
        pending.append({'run_id': run_id, 'status': manifest.get('status', 'not_started')})
if args.require_all and pending:
    raise SystemExit('Incomplete campaign; no final audit written: ' + json.dumps(pending))

audits = []
reviewed_runner = sha(ROOT / 'eval/src/runner.ts')
for directory, manifest in finished:
    data_path = datasets[manifest['dataset_sha256']]
    subprocess.run(['node', str(ROOT / 'scripts/audit-http-trace.mjs'),
                    '--run-id', manifest['run_id'], '--data', str(data_path),
                    '--output', str(directory / 'http-trace-audit.json')],
                   cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    trace = json.loads((directory / 'http-trace-audit.json').read_text())
    runner = subprocess.check_output(['git', 'show', manifest['eval_commit'] + ':src/runner.ts'],
                                     cwd=ROOT / 'eval')
    assert hashlib.sha256(runner).hexdigest() == reviewed_runner
    samples = {s['sample_id']: s for s in json.loads(data_path.read_text())}
    questions = [q for sid in manifest['samples'] for q in samples[sid]['questions']]
    assert len(questions) == manifest['planned_questions']
    judgments = rows(directory / 'judgments.jsonl')
    assert len(judgments) == len({j['qid'] for j in judgments})
    assert {j['qid'] for j in judgments} == {q['qid'] for q in questions}
    option_elements = [v for q in questions for v in q.get('options', [])]
    assert all(isinstance(v, str) for v in option_elements)
    retrievals = rows(directory / 'retrievals.jsonl')
    assert {j['qid'] for j in judgments if j['status'] == 'judged'} <= {r['qid'] for r in retrievals}
    ingest = rows(directory / 'ingest.jsonl')
    contents = [[m['content'] for m in row['memories']] for row in retrievals]
    total = sum(map(len, contents))
    duplicates = sum(len(cs) - len(set(cs)) for cs in contents)
    baseline = manifest['run_id'].startswith('baseline-v7-')
    token_code = ("import {readFileSync} from 'node:fs';import {estimateTokens} from "
                  + json.dumps((ROOT / 'service/dist/text.js').as_uri()) + ";"
                  + "const rows=JSON.parse(readFileSync(0,'utf8'));"
                  + "console.log(JSON.stringify(rows.map(cs=>cs.reduce((n,c)=>n+"
                  + ("Math.ceil(c.length/3)" if baseline else "estimateTokens(c)")
                  + ",0))));")
    token_estimates = json.loads(subprocess.check_output(
        ['node', '--input-type=module', '-e', token_code], input=json.dumps(contents), text=True))
    audits.append({
        'run_id': manifest['run_id'], 'http_trace': trace,
        'answer_runner_matches_reviewed_source': True,
        'terminal_qid_coverage_exact': True, 'option_elements': len(option_elements),
        'options_are_plain_strings': True,
        'terminal_statuses': dict(collections.Counter(j['status'] for j in judgments)),
        'ingestion_attempt_statuses': dict(collections.Counter(i['status'] for i in ingest)),
        'add_attempt_ms': distribution([i['elapsed_ms'] for i in ingest]),
        'successful_search_ms': distribution([row['elapsed_ms'] for row in retrievals]),
        'returned_evidence': {
            'count': distribution([len(cs) for cs in contents]),
            'estimated_tokens': distribution(token_estimates),
            'utf8_bytes': distribution([sum(len(c.encode()) for c in cs) for cs in contents]),
            'exact_duplicate_fraction_within_response': duplicates / total if total else None,
            'token_scope': ('Original baseline ceil(JS UTF-16 length / 3) budget heuristic.' if baseline else
                            'Actual service estimateTokens function including Han adjustment.') +
                           ' Per-item estimates only; excludes prompt/JSON overhead and is not tokenizer billing.',
            'deduplication_scope': 'Residual exact duplicate output text only; no claim about unseen candidate removal.',
            'source_id_comparability': 'N/A for semantic comparison: unmodified mem0 does not guarantee original source-ID retention.'
                if baseline else 'Source-ID coverage is available as an identifier diagnostic, not entailment.'},
        'input_sha256': {name: sha(directory / name) if (directory / name).exists() else None for name in
                         ['manifest.json', 'requests.jsonl', 'ingest.jsonl', 'retrievals.jsonl', 'judgments.jsonl']}})

report = {'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'complete_matrix': not pending, 'expected_runs': len(expected), 'audited_runs': len(audits),
          'pending': pending, 'reviewed_runner_sha256': reviewed_runner,
          'token_estimator_sha256': sha(ROOT / 'service/dist/text.js'),
          'audit_script_sha256': sha(pathlib.Path(__file__)), 'runs': audits,
          'scope': 'Saved service-bound requests and source boundary audit. Errors remain terminal attempts; '
                   'no partial scores read, no model calls, no claim that source review is network capture.'}
output = args.output or ROOT / 'reports/completed-run-audit.json'
if args.run_id and output.resolve() == (ROOT / 'reports/completed-run-audit.json').resolve():
    raise SystemExit('Explicit runs must not overwrite the historical matrix audit')
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'audited_runs': len(audits), 'expected_runs': len(expected), 'pending': pending}))
