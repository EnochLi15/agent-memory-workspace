"""Compare two frozen audits without replacing verdicts or dropping unresolved cases."""
import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def rows(path):
    result = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row['qid'] in result:
            raise ValueError('Duplicate qid: ' + row['qid'])
        result[row['qid']] = row
    return result


def compare(left, right, packet):
    if set(left) != set(right) or set(left) != set(packet):
        raise ValueError('Audits must retain the complete identical planned denominator')
    pairs, by_type, review = Counter(), defaultdict(Counter), []
    for qid, item in packet.items():
        a, b = left[qid], right[qid]
        for row in (a, b):
            if row['evaluation_type'] != item['evaluation_type']:
                raise ValueError('Evaluation type mismatch: ' + qid)
            if row['status'] == 'judged' and type(row.get('correct')) is not bool:
                raise ValueError('Judged verdict is not boolean: ' + qid)
        label = lambda r: str(r['correct']).lower() if r['status'] == 'judged' else r['status']
        key = label(a) + ' / ' + label(b)
        pairs[key] += 1
        by_type[item['evaluation_type']][key] += 1
        if a['status'] != 'judged' or b['status'] != 'judged' or a['correct'] != b['correct']:
            review.append({**item, 'left_audit': a, 'right_audit': b, 'review_status': 'pending'})
    return {'planned': len(packet), 'joint_verdicts': dict(pairs), 'by_type': dict(by_type),
            'pending_review': len(review), 'independent_human_reviews': 0}, review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('merged', 'independent', 'packet', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    merged, independent = read(args.merged/'manifest.json'), read(args.independent/'manifest.json')
    if merged['protocol'] != 'transport-only-audit-merge-v1' or independent['status'] not in ('finished', 'incomplete') or not independent.get('finished_at'):
        raise ValueError('Requires a verified merged audit and a completed independent audit')
    for name, checksum in merged['input_hashes'].items():
        if sha(Path(name)) != checksum:
            raise ValueError('Merged input checksum mismatch: ' + name)
    original = read(Path(merged['original'])/'manifest.json')
    for key in ('protocol', 'prompt_sha256', 'source_sha256', 'model_runtime_sha256', 'inference', 'input_sha256', 'planned'):
        if original[key] != independent[key]:
            raise ValueError('Audit protocol/input changed: ' + key)
    paths = [args.merged/'results.jsonl', args.independent/'results.jsonl']
    for manifest, path in zip((merged, independent), paths):
        if manifest['results_sha256'] != sha(path):
            raise ValueError('Audit results checksum mismatch')
    if sha(args.packet/'blind.jsonl') != independent['input_sha256']:
        raise ValueError('Packet differs from audited input')
    packet = rows(args.packet/'blind.jsonl')
    if len(packet) != independent['planned']:
        raise ValueError('Manifest planned denominator mismatch')
    summary, pending = compare(rows(paths[0]), rows(paths[1]), packet)
    summary.update(protocol='independent-criterion-audit-comparison-v1', left_model=original['model'], right_model=independent['model'],
                   scope='Paired audits of selected exposed saved answers. Disagreements and unresolved cases remain pending; neither judge is treated as ground truth. No historical score replacement.')
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    (args.output/'pending-review.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in pending))
    inputs = paths + [args.merged/'manifest.json', args.independent/'manifest.json', args.packet/'blind.jsonl']
    (args.output/'manifest.json').write_text(json.dumps({'source_sha256': sha(Path(__file__)), 'input_hashes': {str(p): sha(p) for p in inputs}}, indent=2)+'\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
