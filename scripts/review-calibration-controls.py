"""Check a completed v3 audit against withheld synthetic control labels."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('controls', 'run', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    manifest_path, results_path = args.run/'manifest.json', args.run/'results.jsonl'
    manifest = json.loads(manifest_path.read_text())
    if manifest['protocol'] != 'semantic-criteria-span-audit-v3' or not manifest.get('finished_at'):
        raise ValueError('Requires a completed v3 audit attempt')
    if sha(args.controls) != manifest['input_sha256'] or sha(results_path) != manifest['results_sha256']:
        raise ValueError('Input or results changed')
    controls = json.loads(args.controls.read_text())['cases']
    rows = [json.loads(line) for line in results_path.read_text().splitlines() if line.strip()]
    by_id = {row['qid']: row for row in rows}
    if len(by_id) != len(rows) or len(rows) != manifest['planned'] or len(controls) != len(rows) or set(by_id) != {c['qid'] for c in controls}:
        raise ValueError('Control denominator or identities differ')
    checks = []
    for case in controls:
        row = by_id[case['qid']]
        criteria = {c['id']: c for c in row.get('criteria', [])}
        label_match = row['status'] == 'judged' and row.get('correct') is case['expected_correct']
        requirement_match = all(criteria.get(key, {}).get('requirement') == value for key, value in case.get('expected_requirements', {}).items())
        spans = row.get('answer_spans', [])
        # The JavaScript protocol stores UTF-16 offsets, including surrogate pairs.
        answer = case['answer'].encode('utf-16-le')
        citations_match = all(span['id'] == i and answer[2*span['start']:2*span['end']].decode('utf-16-le') == span['text'] for i, span in enumerate(spans))
        if row['status'] == 'judged':
            citations_match = citations_match and all(c['answer_quotes'] == [spans[i]['text'] for i in c['answer_span_ids']] for c in criteria.values())
        checks.append({'qid': case['qid'], 'status': row['status'], 'expected_correct': case['expected_correct'], 'actual_correct': row.get('correct'), 'label_match': label_match, 'requirement_match': requirement_match, 'citations_match': citations_match, 'pass': label_match and requirement_match and citations_match})
    report = {'protocol': 'v3-calibration-control-review-v1', 'input_hashes': {str(p): sha(p) for p in (args.controls, manifest_path, results_path)}, 'source_sha256': sha(Path(__file__)), 'model': manifest['model'], 'planned': len(controls), 'status_counts': dict(Counter(r['status'] for r in rows)), 'passed': sum(c['pass'] for c in checks), 'gate_pass': all(c['pass'] for c in checks), 'checks': checks, 'independent_human_labels': 0, 'scope': 'Assistant-labeled synthetic controls and exposed saved-answer cases from the declared input set. Checks label agreement, omission/prohibition polarity and exact answer citation reconstruction. Not a benchmark accuracy or proof that all semantic judgments are correct.'}
    with args.output.open('x') as f:
        f.write(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: v for k, v in report.items() if k not in ('checks', 'input_hashes')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
