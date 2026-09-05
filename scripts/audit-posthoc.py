"""Verify posthoc coverage and evidence quotes without changing any score."""
import argparse
import collections
import datetime
import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--run-id', required=True)
parser.add_argument('--data', required=True)
parser.add_argument('--require-hypotheses', action='store_true')
parser.add_argument('--require-upstream', action='store_true')
args = parser.parse_args()
if not args.run_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.' for c in args.run_id):
    raise SystemExit('Invalid run ID')
directory = ROOT / 'eval/artifacts' / args.run_id


def read(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


manifest = read(directory / 'manifest.json')
if manifest['status'] != 'finished':
    raise SystemExit('Wait for a finished evaluation')
data = pathlib.Path(args.data)
assert sha(data) == manifest['dataset_sha256']
samples = {s['sample_id']: s for s in read(data)}
questions = [q for sid in manifest['samples'] for q in samples[sid]['questions']]
assert len(questions) == manifest['planned_questions']
judgments = rows(directory / 'judgments.jsonl')
assert len(judgments) == len({j['qid'] for j in judgments})
assert {j['qid'] for j in judgments} == {q['qid'] for q in questions}
predictions = rows(directory / 'predictions.jsonl')
assert len(predictions) == len({p['qid'] for p in predictions})
assert {p['qid'] for p in predictions} <= {q['qid'] for q in questions}
report = {'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'run_id': args.run_id, 'original_planned': len(questions),
          'primary_statuses': dict(collections.Counter(j['status'] for j in judgments)),
          'saved_predictions': len(predictions), 'audit_script_sha256': sha(pathlib.Path(__file__)),
          'input_sha256': {name: sha(directory / name) for name in
                          ['manifest.json', 'judgments.jsonl', 'predictions.jsonl', 'retrievals.jsonl']}}

hypotheses = directory / 'diagnostic-hypotheses'
if args.require_hypotheses and not (hypotheses / 'summary.json').exists():
    raise SystemExit('Wait for all wrong-answer hypotheses')
if (hypotheses / 'summary.json').exists():
    hs = rows(hypotheses / 'questions.jsonl')
    expected = {j['qid'] for j in judgments if j['status'] == 'judged' and not j['correct']}
    assert len(hs) == len(expected) and {h['qid'] for h in hs} == expected
    retrievals = {r['qid']: r for r in rows(directory / 'retrievals.jsonl')}
    accepted = rejected = warnings = 0
    for h in hs:
        for quote in h.get('evidence_quotes', []):
            assert quote and any(quote in m['content'] for m in retrievals[h['qid']]['memories'])
            accepted += 1
        rejected += len(h.get('rejected_quote_proposals', []))
        warnings += bool(h.get('schema_warnings'))
    hm = read(hypotheses / 'manifest.json')
    assert hm['dataset_sha256'] == sha(data)
    assert hm['judgments_sha256'] == sha(directory / 'judgments.jsonl')
    summary = read(hypotheses / 'summary.json')
    observed = collections.Counter(h['primary'] if h['status'] == 'hypothesis' else h['status'] for h in hs)
    assert summary['planned'] == len(hs) and dict(observed) == summary['counts']
    report['hypotheses'] = {'coverage_exact': True, 'summary': summary,
                            'accepted_quotes': accepted, 'rejected_quote_proposals': rejected,
                            'rows_with_schema_warnings': warnings,
                            'accepted_quotes_are_source_substrings': True,
                            'questions_sha256': sha(hypotheses / 'questions.jsonl'),
                            'scope': 'Hypotheses only; exact quotes do not establish causal explanations or justify changing scores.'}

upstream = directory / 'upstream-memops-diagnostics'
if args.require_upstream and not (upstream / 'summary.json').exists():
    raise SystemExit('Wait for upstream MemOps diagnostics')
if (upstream / 'summary.json').exists():
    results = rows(upstream / 'results.jsonl')
    expected = {p['qid'] for p in predictions if p['benchmark'] == 'memops'}
    assert len(results) == len(expected) and {r['question_id'] for r in results} == expected
    um = read(upstream / 'manifest.json')
    assert um['prediction_sha256'] == sha(directory / 'predictions.jsonl')
    assert um['script_sha256'] == sha(ROOT / 'eval/python/upstream/memops/operation_metrics.py')
    for name, digest in um['gold_files'].items():
        assert name == pathlib.Path(name).name and sha(upstream / name) == digest
    judged = [r for r in results if r['status'] == 'judged']
    assert all(r.get('answer_score') in [0, 1] for r in judged)
    summary = read(upstream / 'summary.json')
    assert summary['planned'] == len(results) and summary['judged'] == len(judged)
    assert summary['answer_correct'] == sum(r['answer_score'] for r in judged)
    missing = [j for j in judgments if j['qid'] not in expected]
    flags = {}
    for name in ['leakage', 'over_forget', 'stale_value']:
        values = [r[name] for r in judged if r.get(name) is not None]
        assert all(v in [0, 1] for v in values)
        flags[name] = {'flagged': sum(values), 'applicable_judged_answers': len(values)}
    report['upstream_memops'] = {'coverage_of_saved_answers_exact': True,
                                'original_planned': len(questions), 'saved_answers': len(expected),
                                'diagnostic_judged': len(judged),
                                'diagnostic_errors': len(results) - len(judged),
                                'answer_correct': summary['answer_correct'],
                                'missing_predictions': len(missing),
                                'missing_prediction_primary_statuses': dict(collections.Counter(j['status'] for j in missing)),
                                'missing_prediction_qids': [j['qid'] for j in missing],
                                'binary_answer_diagnostic_flags': flags,
                                'upstream_summary': summary,
                                'results_sha256': sha(upstream / 'results.jsonl'),
                                'manifest_sha256': sha(upstream / 'manifest.json'),
                                'scope': 'Saved-answer diagnostics only. Questions without predictions were never judged here; '
                                         'they remain in the original planned denominator. Flags describe answers, not database erasure/retention.'}
output = directory / 'posthoc-audit.json'
output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'run_id': args.run_id, 'original_planned': len(questions), 'saved_predictions': len(predictions),
                  'hypotheses_audited': 'hypotheses' in report, 'upstream_audited': 'upstream_memops' in report}))
