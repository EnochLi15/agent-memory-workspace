"""Compare frozen v2/v3 audits of identical saved answers, retaining every case."""
import argparse
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('criterion_comparison', Path(__file__).with_name('compare-criterion-audits.py'))
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('v2', 'v3', 'packet', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    manifests = [shared.read(p/'manifest.json') for p in (args.v2, args.v3)]
    for manifest, protocol in zip(manifests, ('semantic-criteria-audit-v2', 'semantic-criteria-span-audit-v3')):
        if manifest['protocol'] != protocol or not manifest.get('finished_at') or manifest['status'] not in ('finished', 'incomplete'):
            raise ValueError('Requires completed v2 and v3 audit attempts')
    for field in ('input_sha256', 'planned', 'model', 'base', 'inference', 'model_runtime_sha256'):
        if manifests[0][field] != manifests[1][field]:
            raise ValueError('Paired input/inference mismatch: ' + field)
    if shared.sha(args.packet) != manifests[0]['input_sha256']:
        raise ValueError('Packet checksum mismatch')
    paths = [p/'results.jsonl' for p in (args.v2, args.v3)]
    for manifest, path in zip(manifests, paths):
        if shared.sha(path) != manifest['results_sha256']:
            raise ValueError('Result checksum mismatch')
    packet = shared.rows(args.packet)
    if len(packet) != manifests[0]['planned']:
        raise ValueError('Planned denominator mismatch')
    left, right = map(shared.rows, paths)
    summary, pending = shared.compare(left, right, packet)
    old_errors = [qid for qid, row in left.items() if row['status'] == 'protocol_error']
    summary.update(
        protocol='paired-protocol-revision-audit-v1',
        model=manifests[0]['model'],
        old_protocol_errors=[{'qid': qid, 'v3_status': right[qid]['status'], 'v3_correct': right[qid]['correct']} for qid in old_errors],
        scope='One v2 and one v3 model audit of identical exposed saved answers. Protocol revision and sampling both vary; no causal estimate, independent human truth, or replacement benchmark accuracy. All unresolved cases and changed verdicts remain reviewable.')
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    (args.output/'pending-review.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in pending))
    inputs = paths + [p/'manifest.json' for p in (args.v2, args.v3)] + [args.packet]
    (args.output/'manifest.json').write_text(json.dumps({'source_sha256': shared.sha(Path(__file__)), 'shared_source_sha256': shared.sha(Path(__file__).with_name('compare-criterion-audits.py')), 'input_hashes': {str(p): shared.sha(p) for p in inputs}}, indent=2)+'\n')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
