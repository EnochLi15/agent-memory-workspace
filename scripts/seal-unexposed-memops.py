"""Seal a reproducible background-disjoint cohort without inspecting answer text.

Selection depends only on background/sample identity, operation and a fixed seed.
All sessions and questions of each selected sample are copied without filtering.
The normalized corpus is parsed to copy records, but no question, answer, rubric
or conversation text is printed or included in the public manifest.
"""
import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

OPERATIONS = ('remember', 'forget', 'update', 'reflect', 'trajectory_ops')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def select(samples, audit, seed, per_operation):
    if per_operation < 1:
        raise ValueError('per-operation must be positive')
    eligible = set(audit['no_recorded_exposure_groups'])
    exposed = set(audit['recorded_exposed_groups'])
    if eligible & exposed:
        raise ValueError('Exposure sets overlap')
    ids = [s['sample_id'] for s in samples]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate sample identity')
    pool = []
    for s in samples:
        group, operation = s['sample_id'].split('_', 1)
        if group != s['group_id'] or operation not in OPERATIONS:
            raise ValueError('Invalid background or operation identity')
        if group in eligible:
            if s['sample_id'] + '.json' not in audit['no_recorded_exposure_files']:
                raise ValueError('Sample not covered by source inventory')
            pool.append((s, operation))
    chosen, reserved = [], set()
    for round_index in range(per_operation):
        for operation in OPERATIONS:
            candidates = [s for s, op in pool if op == operation and s['group_id'] not in reserved]
            if not candidates:
                raise ValueError('Insufficient distinct unexposed groups for ' + operation)
            pick = min(candidates, key=lambda s: sha(f'{seed}:{round_index}:{operation}:{s["sample_id"]}'.encode()))
            reserved.add(pick['group_id'])
            chosen.append(pick)
    qids = [q['qid'] for s in chosen for q in s['questions']]
    if len(qids) != len(set(qids)):
        raise ValueError('Duplicate selected question identity')
    if any(not s['sessions'] or not s['questions'] for s in chosen):
        raise ValueError('Empty selected sample')
    return chosen


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--normalized', type=Path, required=True)
    p.add_argument('--exposure', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--seed', default='round2-confirmation-20260906-v1')
    p.add_argument('--per-operation', type=int, default=3)
    args = p.parse_args()
    source_bytes, audit_bytes = args.normalized.read_bytes(), args.exposure.read_bytes()
    samples, audit = json.loads(source_bytes), json.loads(audit_bytes)
    if audit.get('protocol') != 'recorded-background-exposure-v1':
        raise ValueError('Unsupported exposure audit')
    # Audit file identities are relative to the workspace root. A changed log
    # requires a new audit, not an assumption that the eligible set is current.
    for path, expected in audit['input_sha256'].items():
        if sha(Path(path).read_bytes()) != expected:
            raise ValueError('Exposure evidence changed: ' + path)
    chosen = select(samples, audit, args.seed, args.per_operation)
    payload = (json.dumps(chosen, ensure_ascii=False, indent=2) + '\n').encode()
    manifest = {
        'protocol': 'sealed-memops-background-cohort-v1',
        'seed': args.seed, 'per_operation': args.per_operation,
        'normalized_sha256': sha(source_bytes), 'exposure_sha256': sha(audit_bytes),
        'dataset_sha256': sha(payload), 'dataset_path': str(args.output / 'memops.json'),
        'selection_fields': ['sample_id', 'group_id', 'operation'],
        'content_printed': False, 'full_sessions_and_questions_preserved': True,
        'reserved_groups': sorted(s['group_id'] for s in chosen),
        'samples': [{'sample_id': s['sample_id'], 'group_id': s['group_id'],
                     'sessions': len(s['sessions']), 'questions': len(s['questions']),
                     'record_sha256': sha(json.dumps(s, sort_keys=True, ensure_ascii=False).encode())}
                    for s in chosen],
        'planned_questions': sum(len(s['questions']) for s in chosen),
        'planned_sessions': sum(len(s['sessions']) for s in chosen),
        'category_counts': dict(sorted(Counter(q['category'] for s in chosen for q in s['questions']).items())),
        'policy': ['All variants of each selected background are reserved from further development.',
                   'Do not inspect questions, references or rubrics before configuration freeze and confirmation.',
                   'All errors remain in the planned denominator. No replacement of difficult backgrounds.',
                   'No recorded exposure is not proof against unlogged inspection or model pretraining.',
                   'Independent human judge calibration and complete development validation remain prerequisites.'],
    }
    if args.output.exists() or args.report.exists():
        raise ValueError('Refusing to overwrite an existing sealed cohort or report')
    args.output.mkdir(parents=True, mode=0o700)
    with os.fdopen(os.open(args.output / 'memops.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as f:
        f.write(payload)
    encoded = json.dumps(manifest, ensure_ascii=False, indent=2) + '\n'
    (args.output / 'manifest.json').write_text(encoded)
    with args.report.open('x') as f:
        f.write(encoded)
    print(json.dumps({'status': 'sealed', 'samples': len(chosen), 'groups': len(manifest['reserved_groups']),
                      'questions': manifest['planned_questions'], 'sessions': manifest['planned_sessions'],
                      'dataset_sha256': manifest['dataset_sha256']}))


if __name__ == '__main__':
    main()
