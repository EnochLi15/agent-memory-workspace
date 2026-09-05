"""Summarize audited saved-answer diagnostics separately from primary scores."""
import collections
import datetime
import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


reports = {}
for benchmark in ['locomo', 'memops']:
    directory = ROOT / 'eval/artifacts' / ('holdout-v2-U3-' + benchmark)
    audit_path = directory / 'posthoc-audit.json'
    if not audit_path.exists():
        raise SystemExit('Complete and audit ' + benchmark + ' diagnostics first')
    audit = read(audit_path)
    hs = audit['hypotheses']
    if not hs['coverage_exact'] or not hs['accepted_quotes_are_source_substrings']:
        raise SystemExit('Require complete source quote verification')
    if hs['questions_sha256'] != sha(directory / 'diagnostic-hypotheses/questions.jsonl'):
        raise SystemExit('Hypotheses changed after audit')
    result = {'audit_path': str(audit_path.relative_to(ROOT)), 'audit_sha256': sha(audit_path),
              'original_planned': audit['original_planned'],
              'primary_statuses': audit['primary_statuses'], 'hypotheses': hs}
    if benchmark == 'memops':
        upstream = audit['upstream_memops']
        us = rows(directory / 'upstream-memops-diagnostics/results.jsonl')
        js = {j['qid']: j for j in rows(directory / 'judgments.jsonl')}
        if upstream['results_sha256'] != sha(directory / 'upstream-memops-diagnostics/results.jsonl'):
            raise SystemExit('Upstream results changed after audit')
        table = collections.Counter()
        slices = {}
        disagreements = []
        for u in us:
            p = js[u['question_id']]
            if p['status'] != 'judged' or u['status'] != 'judged':
                continue
            a, b = bool(p['correct']), bool(u['answer_score'])
            table['primary_' + str(a).lower() + '_upstream_' + str(b).lower()] += 1
            operation = u['operation_type']
            slice_ = slices.setdefault(operation, {'jointly_judged': 0, 'primary_correct': 0, 'upstream_correct': 0})
            slice_['jointly_judged'] += 1
            slice_['primary_correct'] += a
            slice_['upstream_correct'] += b
            if a != b:
                disagreements.append({'qid': p['qid'], 'primary_correct': a, 'upstream_correct': b})
        result.update(upstream=upstream, paired_judgment_table=dict(table),
                      paired_operation_slices=slices, disagreements=disagreements,
                      comparison_scope='Same saved predictions; different judging protocols. '
                      'No regenerated answers, no service calls, no change to primary scores. '
                      'Missing predictions remain absent from this paired subset.')
    reports[benchmark] = result

output = {'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'script_sha256': sha(pathlib.Path(__file__)), 'benchmarks': reports,
          'scope': 'Completed posthoc evidence only. Model explanations are hypotheses, '
                   'and lifecycle flags describe answers rather than database deletion.'}
(ROOT / 'reports/holdout-v2-final-diagnostics.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({b: {'hypotheses': r['hypotheses']['summary'],
                     'paired_judgment_table': r.get('paired_judgment_table')} for b, r in reports.items()}))
