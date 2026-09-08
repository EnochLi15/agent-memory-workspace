"""Compare completed frozen-history quality runs without changing their verdicts."""
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[2]
PAIRS = [
    ('locomo', 'priority-conv30-72-20260908-02', 'priority-quality-locomo-20260908-01'),
    ('memops', 'priority-memops-four-20260908-01', 'priority-quality-memops-20260908-01'),
]


def read(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def score(judgments):
    slices = {}
    for row in judgments:
        s = slices.setdefault(row['category'], {'planned': 0, 'correct': 0})
        s['planned'] += 1
        s['correct'] += row.get('correct') is True
    correct = sum(row.get('correct') is True for row in judgments)
    return {'planned': len(judgments), 'correct': correct, 'accuracy': correct / len(judgments),
            'status_counts': dict(Counter(row['status'] for row in judgments)), 'slices': slices}


def main():
    report = {'generated_at': datetime.now(timezone.utc).isoformat(), 'scope':
              'Paired QA on cloned frozen ingestion: 72 LoCoMo questions in one conversation and '
              '20 MemOps questions in four operation samples. Not the 972-question selected-suite score. '
              'Answer and Judge remain stochastic; this is one observed run per candidate, not a causal effect estimate.',
              'comparisons': []}
    for benchmark, baseline, candidate in PAIRS:
        old_dir = ROOT / 'eval/artifacts' / f'{baseline}-candidate-{benchmark}'
        new_dir = ROOT / 'eval/artifacts' / f'{candidate}-candidate-{benchmark}'
        old_manifest, new_manifest = read(old_dir / 'manifest.json'), read(new_dir / 'manifest.json')
        if new_manifest['status'] != 'finished':
            print(json.dumps({'status': 'pending', 'campaign': candidate,
                              'recorded': len(rows(new_dir / 'judgments.jsonl'))}))
            return
        runtime = read(ROOT / 'artifacts' / candidate / 'candidate-runtime.json')
        assert runtime['status'] == 'finished'
        keys = ['dataset_sha256', 'answer_model', 'answer_inference', 'judge_model', 'judge_kind',
                'answer_prompt_sha256', 'rubric_prompt_sha256', 'contract_sha256', 'split_sha256',
                'seed', 'top_k', 'chunk_messages', 'chunk_words', 'mode', 'memory_namespace', 'eval_commit']
        assert all(old_manifest[k] == new_manifest[k] for k in keys), 'A frozen comparison input changed'
        assert old_manifest['source_state']['eval']['patch_sha256'] == new_manifest['source_state']['eval']['patch_sha256'], 'Evaluator working-tree implementation changed'
        old, new = rows(old_dir / 'judgments.jsonl'), rows(new_dir / 'judgments.jsonl')
        before, after = {r['qid']: r for r in old}, {r['qid']: r for r in new}
        assert len(before) == len(old) == len(after) == len(new) == new_manifest['planned_questions']
        assert before.keys() == after.keys()
        calls = rows(ROOT / 'artifacts' / candidate / 'candidate-model-calls.jsonl')
        assert not any(r['kind'] == 'generation' for r in calls), 'QA unexpectedly invoked write/rerank models'
        receipts = rows(new_dir / 'ingest.jsonl')
        assert all(r['status'] == 'ok' for r in receipts)
        predictions = {r['qid']: r['answer'] for r in rows(new_dir / 'predictions.jsonl')}
        original_predictions = {r['qid']: r['answer'] for r in rows(old_dir / 'predictions.jsonl')}
        retrievals = {r['qid']: r for r in rows(new_dir / 'retrievals.jsonl')}
        changes = [{'qid': q, 'category': after[q]['category'], 'before': before[q].get('correct') is True,
                    'after': after[q].get('correct') is True, 'candidate_status': after[q]['status'], 'baseline_answer': original_predictions.get(q),
                    'candidate_answer': predictions.get(q), 'candidate_judgment': after[q].get('raw')}
                   for q in before if (before[q].get('correct') is True) != (after[q].get('correct') is True)]
        start, end = (datetime.fromisoformat(new_manifest[k].replace('Z', '+00:00'))
                      for k in ['started_at', 'finished_at'])
        report['comparisons'].append({'benchmark': benchmark, 'baseline': baseline, 'candidate': candidate,
             'matching_inputs': {k: new_manifest[k] for k in keys}, 'baseline_score': score(old),
             'candidate_score': score(new), 'improved': sum(c['after'] is True for c in changes),
             'regressed': sum(c['before'] is True for c in changes), 'changes': changes,
             'snapshot_audit': {'http_receipts': len(receipts), 'write_model_calls': 0,
                 'service_model_calls': dict(Counter(r.get('purpose', r['kind']) for r in calls))},
             'elapsed_seconds': (end-start).total_seconds(),
             'search_mean_ms': mean(r['elapsed_ms'] for r in retrievals.values()),
             'infrastructure_errors': [{**r, 'retrieval_recorded': r['qid'] in retrievals,
                                        'answer_recorded': r['qid'] in predictions}
                                       for r in new if r['status'] != 'judged'],
             'remaining_wrong': [r['qid'] for r in new if r.get('correct') is not True]})
    total = sum(c['candidate_score']['planned'] for c in report['comparisons'])
    before = sum(c['baseline_score']['correct'] for c in report['comparisons'])
    after = sum(c['candidate_score']['correct'] for c in report['comparisons'])
    report['pilot_total'] = {'planned': total, 'before_correct': before, 'after_correct': after,
                            'before_accuracy': before/total, 'after_accuracy': after/total}
    frozen = ROOT / 'artifacts/priority-quality-fix-20260908/code-snapshot.json'
    report['candidate_artifact'] = {'manifest_sha256': hashlib.sha256(frozen.read_bytes()).hexdigest(),
                                    'manifest': str(frozen), 'scope': read(frozen)['scope'],
                                    'excludes': 'Concurrent uncommitted erasure fixes in extraction.ts, erasure.ts and storage.ts after this artifact was frozen.'}
    report['recovery'] = read(ROOT / 'reports/priority-quality-recovery-20260908.json')
    report['historical_followup'] = {
        'report': str(ROOT / 'reports/priority-history-followup-20260908.json'),
        'scope': 'A later quoted-statement fix passed one independent targeted QA. Its verdict is not inserted into the 92-question v1 total.'}
    report['provider_replay_interpretation'] = {
        'observed': 'The exact original request again returned HTTP 400 with provider_error_code=1301 before streaming, in about 1.5 seconds.',
        'official_meaning': 'The provider reports possible unsafe or sensitive input/generated content.',
        'source': 'https://docs.bigmodel.cn/cn/api/api-code',
        'limit': 'The original run did not record its provider code. This diagnoses the new exact-input replay; it does not identify a particular triggering passage or prove the original code.'}
    out = ROOT / 'reports/priority-quality-comparison-20260908.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'report': str(out), 'pilot_total': report['pilot_total'], 'benchmarks':
                      [{k: c[k] for k in ['benchmark','baseline_score','candidate_score','improved','regressed']} for c in report['comparisons']]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
