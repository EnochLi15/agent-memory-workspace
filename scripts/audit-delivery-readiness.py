"""Validate completed evidence before packaging; this does not validate a future archive."""
import datetime
import hashlib
import json
import pathlib
import subprocess
import urllib.request
import argparse

ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(name):
    if not (ROOT / name).exists():
        raise SystemExit('Required completed evidence is not available yet: ' + name)
    return json.loads((ROOT / name).read_text())


def sha(name):
    digest = hashlib.sha256()
    with (ROOT / name).open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def git(repo, *args):
    return subprocess.check_output(['git', *args], cwd=ROOT / repo, text=True).strip()


def require(condition, explanation):
    if not condition:
        raise SystemExit(explanation)


evidence = set()


def document(name):
    evidence.add(name)
    return read(name)


def audit_v1(release_path, output):
    """V1 gates are explicit; legacy benchmark matrices are not V1 evidence."""
    release_name = str(release_path.resolve().relative_to(ROOT))
    release = document(release_name)
    require(release.get('protocol') == 'v1-release-manifest-v1', 'Unknown release protocol')
    require(release.get('human_labels') == 0, 'This release has no independent human calibration')

    def bound(name):
        path = pathlib.PurePosixPath(name)
        require(not path.is_absolute() and '..' not in path.parts, 'Evidence must be workspace-relative')
        require((ROOT / path).resolve().is_relative_to(ROOT), 'Evidence escapes workspace')
        require((ROOT / path).is_file(), 'Required completed evidence is not available yet: ' + name)
        evidence.add(name)
        return sha(name)

    def checked(name):
        bound(name)
        result = document(name)
        for path, expected in result.get('evidence_sha256', {}).items():
            require(bound(path) == expected, 'Evidence changed: ' + path)
        return result

    for repo in ['service', 'eval']:
        require(git(repo, 'rev-parse', 'HEAD') == release['commits'][repo], 'Release source changed: ' + repo)
        require(not git(repo, 'status', '--porcelain'), 'Release source is dirty: ' + repo)
    require(sha('service/contracts/contract.json') == sha('eval/contracts/contract.json'), 'Contract copies differ')
    evidence.update(['service/contracts/contract.json', 'eval/contracts/contract.json'])

    blockers = checked(release['gates']['blockers'])
    require(blockers.get('status') == 'complete' and blockers.get('unresolved_p0') == [], 'Resolve known P0 blockers')
    require(len(blockers.get('cases', [])) == 19, 'Retain all 19 original failure mappings')
    require(all(c.get('cause') and c.get('evidence') and c.get('disposition') in
                ['fixed_and_verified', 'valid_rejection_preserved', 'external_failure_bounded']
                for c in blockers['cases']), 'Each original failure needs a verified disposition')
    soak = checked(release['gates']['runtime'])
    require(soak.get('passed') and soak.get('duration_seconds', 0) >= 7500, 'Complete the long runtime gate')
    require(soak.get('runtime_sha256') == sha('scripts/experiment_runtime.py') and
            soak.get('runner_sha256') == sha('scripts/run-experiment.py'), 'Runtime implementation changed after soak')
    state = checked(soak['runtime_record'])
    require(state.get('status') == 'finished' and state.get('exit_code') == 0 and
            not state.get('monitor_gaps') and not state.get('signal'), 'Runtime did not exit cleanly')
    worker = state.get('children', {}).get('model-free-worker', {})
    require(worker.get('exit_code') == 0 and not worker.get('stopped_by_runner'), 'Soak child was interrupted')
    elapsed = (datetime.datetime.fromisoformat(state['finished_at']) -
               datetime.datetime.fromisoformat(state['started_at'])).total_seconds()
    require(elapsed >= 7500 and all(c.get('exit_code') is not None for c in state['children'].values()),
            'Runtime record does not cover the required interval or child exits')

    deploy = document(release['gates']['deployment'])
    require(deploy.get('passed') and deploy.get('no_degraded_write') and
            all(deploy['clean_sources'].values()) and all(deploy['restart_checks'].values()), 'Complete deployment and restart checks')
    require(all(deploy['commits'][r] == release['commits'][r] for r in ['service', 'eval']), 'Deployment tested another source')
    required_contract = {'health', 'echo', 'idempotency', 'read-after-write', 'tenant-isolation', 'top-k',
                         'three-routes', 'validation', 'conflict', 'update-current-state', 'forget-all-paths', 'retained-neighbor'}
    require(deploy['contract'].get('contract') == 'passed' and
            required_contract <= set(deploy['contract']['checks']), 'Incomplete HTTP delivery contract')
    require(deploy['service_tests'] > 0 and deploy['eval_node_tests'] > 0 and deploy['eval_python_tests'] > 0,
            'Missing service/evaluator test results')
    for name, expected in deploy['evidence_sha256'].items():
        path = release['deployment_log_directory'] + '/' + name
        require(bound(path) == expected, 'Deployment log changed: ' + path)
    for name in release['gates']['durability']:
        result = checked(name)
        require(result.get('passed') and result.get('checks') and all(result['checks'].values()), 'Durability check failed: ' + name)

    small = checked(release['gates']['small_pair'])
    require(small.get('passed') and small['storage']['passed'] and small['storage']['count'] == 20 and
            all(v for group in small['storage']['checks'].values() for v in group.values()), 'Small storage gate incomplete')
    require(small['review']['human_labels'] == 0, 'Assistant review cannot be human labels')
    long = checked(release['gates']['long_pair'])
    fixed = document(release['long_dataset_manifest'])
    require(long.get('passed') and long.get('backgrounds') == 4 and
            long.get('questions') == fixed['total_questions'] <= 40, 'Long-background gate incomplete')
    require(long.get('operation_checks') and all(long['operation_checks'].values()), 'Long-background operation checks failed')
    for pair in [small, long]:
        require(all(pair['commits'][r] == release['commits'][r] for r in ['service', 'eval']), 'Pair tested another source')
        require(set(pair['runs']) == {'raw', 'candidate'}, 'Both comparison methods are required')
        for run in pair['runs'].values():
            require(run['finished'] and run['all_adds_ok'] and run['http_audit_passed'] and run['clean_sources'] and
                    run['judged'] == run['planned'] and run['max_items'] <= 32 and run['max_estimated_tokens'] <= 6000,
                    'A paired run is incomplete or outside budget')
        require(not pair['runs']['candidate']['erased_code_returned_qids'], 'Candidate leaked forgotten content')

    completed = checked(release['gates']['full_audit'])
    require(completed['complete_matrix'] and completed['expected_runs'] == completed['audited_runs'] == 2 and
            not completed['pending'], 'Complete and audit both full runs')
    require(completed['audit_script_sha256'] == sha('scripts/audit-completed-runs.py') and
            completed['reviewed_runner_sha256'] == sha('eval/src/runner.ts'), 'Full audit implementation changed')
    audited = {r['run_id']: r for r in completed['runs']}
    require(set(audited) == set(release['primary_runs'].values()) and set(release['primary_runs']) == {'locomo', 'memops'},
            'Full audit must cover exactly this release')
    primary = {}
    for benchmark, run_id in release['primary_runs'].items():
        prefix = 'eval/artifacts/' + run_id + '/'
        m = document(prefix + 'manifest.json'); metrics = document(prefix + 'metrics.json'); a = audited[run_id]
        require(m['status'] == 'finished' and m['planned_questions'] == metrics['planned'] == 500 and
                all(not s['dirty'] for s in m['source_state'].values()), 'Full run is incomplete or started dirty')
        require(all(m[r + '_commit'] == release['commits'][r] for r in ['service', 'eval']), 'Full run tested another source')
        for name in ['scripts/run-experiment.py', 'scripts/experiment_runtime.py',
                     'configs/round2-small-pair-v1.json']:
            frozen = subprocess.check_output(['git', 'show', m['workspace_commit'] + ':' + name], cwd=ROOT)
            require(hashlib.sha256(frozen).hexdigest() == bound(name), 'Execution source changed after full evaluation: ' + name)
        cfg = m['service_configuration']
        require((cfg['maxEvidence'], cfg['tokenBudget'], cfg['addTimeout'], cfg['searchTimeout']) ==
                (32, 6000, 115000, 55000) and not any(cfg[k] for k in ['rerank', 'coveragePacking', 'eventView']) and
                not cfg['experimental']['multiHop'] and not cfg['experimental']['reflection'], 'Frozen V1 budget/configuration changed')
        require(a['http_trace']['passed'] and a['terminal_qid_coverage_exact'] and
                a['answer_runner_matches_reviewed_source'] and a['options_are_plain_strings'], 'Full run boundary audit failed')
        for name, expected in a['input_sha256'].items():
            if expected is None:
                require(name in {'predictions.jsonl', 'retrievals.jsonl'} and not (ROOT / prefix / name).exists(),
                        'Required full audit input missing or changed: ' + name)
            else:
                require(bound(prefix + name) == expected, 'Full audit input changed: ' + name)
        judgments = [json.loads(line) for line in (ROOT / prefix / 'judgments.jsonl').read_text().splitlines() if line.strip()]
        require(len(judgments) == len({r['qid'] for r in judgments}) == 500, 'Every question needs one terminal record')
        correct = sum(r.get('correct') is True for r in judgments)
        judged = sum(r['status'] == 'judged' for r in judgments)
        require(all(r['status'] in {'judged', 'service_error', 'pipeline_error', 'judge_error'} for r in judgments)
                and all(r['status'] == 'judged' for r in judgments if r.get('correct') is True),
                'Invalid terminal status or correctness assigned to an error')
        require((metrics['correct'], metrics['judged'], metrics['unrecorded_questions'], metrics['incomplete']) ==
                (correct, judged, 0, 0), 'Metrics do not match terminal records')
        primary[benchmark] = {'planned': 500, 'correct': correct, 'judged': judged,
                              'accuracy_over_planned': correct / 500, 'statuses': a['terminal_statuses']}
    summary = checked(release['gates']['full_report'])
    require(summary.get('complete') and summary.get('primary_results') == primary and
            summary.get('unresolved_p0') == [] and summary.get('human_labels') == 0,
            'Final report must disclose current results and resolve P0 findings')
    require(summary.get('cost_scope') and summary.get('latency') and summary.get('write_outcomes'), 'Report costs, latency and write failures')

    runtime = checked(release['runtime_bundles'])
    require(runtime.get('archive_config_digests_verified') and runtime['service_commit'] == release['commits']['service'] and
            runtime['eval_commit'] == release['commits']['eval'] and
            deploy['image_id'] in [i['id'] for i in runtime['images']], 'Runtime archive must contain the tested image')
    for entry in runtime['archives']:
        require(bound(entry['path']) == entry['sha256'] and (ROOT / entry['path']).stat().st_size == entry['bytes'], 'Runtime archive changed')
    upstream = document('service/baseline/upstream/source-manifest.json')['files']
    for entry in upstream:
        require(bound('service/baseline/upstream/src/' + entry['path']) == entry['sha256'], 'Original Mem0 source changed')
    require(len(upstream) > 0, 'Missing upstream source inventory')
    for name in release['required_documents']:
        bound(name)
    for name in ['scripts/experiment_runtime.py', 'scripts/run-experiment.py', 'scripts/audit-completed-runs.py',
                 'scripts/audit-http-trace.mjs', 'scripts/package-delivery.py', 'scripts/verify-delivery.py',
                 'scripts/audit-delivery-readiness.py', 'scripts/bundle.py', 'eval/src/runner.ts']:
        bound(name)
    result = {'protocol': 'v1-readiness-audit-v1', 'status': 'ready_for_packaging',
              'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'release_manifest': release_name, 'release_manifest_sha256': sha(release_name),
              'version': release['version'], 'service_commit': release['commits']['service'],
              'eval_commit': release['commits']['eval'], 'primary_results': primary,
              'audit_script_sha256': sha('scripts/audit-delivery-readiness.py'),
              'requirements': ['P0-1', 'P0-2', 'P0-3', 'P0-4', 'P0-5 packaging prerequisites'],
              'scope': 'Pre-archive readiness only. Archive readback, recursive clone and packaged deployment remain required.',
              'evidence_sha256': {name: sha(name) for name in sorted(evidence)}}
    require(not output.exists(), 'Preserve prior readiness report; choose a new output')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'version': result['version'], 'evidence_files': len(evidence)}))


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--release', type=pathlib.Path, help='V1 release manifest; omission retains the historical audit')
parser.add_argument('--output', type=pathlib.Path)
args = parser.parse_args()
if args.release:
    require(args.output is not None, '--release requires a separate --output')
    require(args.output.resolve() != ROOT / 'reports/delivery-readiness-audit.json', 'Preserve historical readiness')
    audit_v1(args.release, args.output)
    raise SystemExit(0)
require(args.output is None, '--output requires --release')


completed = document('reports/completed-run-audit.json')
require(completed['complete_matrix'] and completed['audited_runs'] == 34, 'Complete all 34 run audits')
for run in completed['runs']:
    require(run['http_trace']['passed'] and run['terminal_qid_coverage_exact']
            and run['answer_runner_matches_reviewed_source'] and run['options_are_plain_strings'],
            'Run boundary verification failed: ' + run['run_id'])
    prefix = 'eval/artifacts/' + run['run_id'] + '/'
    for name, digest in run['input_sha256'].items():
        require(sha(prefix + name) == digest, 'Audited input changed: ' + prefix + name)

workflow = document('artifacts/report-workflow.json')['results']
require(len(workflow) == 3 and all(r['status'] == 'complete' for r in workflow), 'Finish posthoc workflow')
baseline = document('artifacts/baseline-upstream-diagnostic-workflow.json')['results']
require(len(baseline) == 2 and all(r['exit_code'] == 0 for r in baseline), 'Finish baseline diagnostics')
usage = document('reports/model-usage-and-resources.json')
require(usage['complete_evaluation_runs'] and usage['complete_memops_posthoc_logs'], 'Finish usage accounting')
for row in usage['service_model_usage'] + usage['saved_memops_posthoc_usage']:
    if 'source' in row:
        require(sha(row['source']) == row['source_sha256'], 'Usage log changed: ' + row['source'])
    else:
        require(row.get('model_call_accounting', '').startswith('N/A:'), 'Missing explicit usage accounting scope')
    if 'service_log_sha256' in row:
        service_log = 'artifacts/' + row['campaign'] + '/' + row['profile'] + '-service.log'
        require(sha(service_log) == row['service_log_sha256'], 'Service degradation log changed')
require(usage['script_sha256'] == sha('scripts/summarize-costs.py'), 'Regenerate usage with current script')

primary = {}
for benchmark, correct, judged in [('locomo', 156, 500), ('memops', 175, 464)]:
    prefix = 'eval/artifacts/holdout-v2-U3-' + benchmark + '/'
    metrics = document(prefix + 'metrics.json')
    manifest = document(prefix + 'manifest.json')
    require((metrics['planned'], metrics['correct'], metrics['judged']) == (500, correct, judged),
            'Primary results differ from the reviewed final report')
    require(manifest['status'] == 'finished' and all(not s['dirty'] for s in manifest['source_state'].values()),
            'Primary candidate must have started clean and finished')
    audit = document(prefix + 'posthoc-audit.json')
    require(audit['hypotheses']['coverage_exact'] and audit['hypotheses']['accepted_quotes_are_source_substrings'],
            'Audit all wrong-answer hypotheses')
    require(audit['hypotheses']['summary']['planned'] == judged - correct, 'Wrong-answer coverage mismatch')
    require(not audit['hypotheses']['summary']['counts'].get('diagnostic_error', 0), 'Resolve diagnostic errors')
    require(sha(prefix + 'diagnostic-hypotheses/questions.jsonl') == audit['hypotheses']['questions_sha256'],
            'Hypotheses changed after audit')
    for name, digest in audit['input_sha256'].items():
        require(sha(prefix + name) == digest, 'Posthoc input changed')
    if benchmark == 'memops':
        upstream = audit['upstream_memops']
        require(upstream['coverage_of_saved_answers_exact'] and upstream['diagnostic_errors'] == 0
                and upstream['saved_answers'] == 464 and upstream['missing_predictions'] == 36,
                'Audit all 464 saved MemOps answers and disclose 36 missing predictions')
        require(sha(prefix + 'upstream-memops-diagnostics/results.jsonl') == upstream['results_sha256'],
                'Upstream diagnostics changed')
    primary[benchmark] = {k: metrics[k] for k in ['planned', 'judged', 'correct', 'accuracy_over_planned']}

diagnostics = document('reports/holdout-v2-final-diagnostics.json')
require(diagnostics['script_sha256'] == sha('scripts/summarize-holdout-diagnostics.py'),
        'Regenerate final diagnostic summary with current script')
for result in diagnostics['benchmarks'].values():
    require(sha(result['audit_path']) == result['audit_sha256'], 'Diagnostic summary refers to a stale audit')

performance = document('artifacts/final-performance/summary.json')
require(document('reports/performance-final.json') == performance, 'Final performance report differs from raw summary')
require(performance['status'] == 'complete' and set(performance['measurements']) == {'primary', 'instrumented'},
        'Complete both final performance measurements')
for name, measurement in performance['measurements'].items():
    metrics = measurement['metrics']
    require(metrics['facts'] == 5000 and metrics['concurrent_searches'] == 16
            and metrics['add_ms']['n'] == 100 and metrics['search_ms']['n'] == 160,
            'Incomplete performance workload')
    require(metrics['add_ms']['max'] < 120000 and metrics['search_ms']['max'] < 60000,
            'Disclose and resolve performance timeout gate')
    require(measurement['performance_script_sha256'] == sha('eval/scripts/performance.mjs'),
            'Performance script changed')
    require(measurement == document('artifacts/final-performance/' + name + '/report.json'),
            'Performance summary/report mismatch')
    require(metrics == document('artifacts/final-performance/' + name + '/metrics.json'),
            'Performance metrics mismatch')
require(performance['measurements']['instrumented']['worker_by_method']
        and performance['measurements']['instrumented']['event_loop_windows']['count'] > 0,
        'Missing scheduling instrumentation')

functional = document('reports/clean-clone-final-functional.json')
require([functional[k] for k in ['service_tests', 'eval_node_tests', 'eval_python_tests', 'baseline_http_guard_tests']]
        == [37, 8, 2, 2], 'Missing full functional checks')
require(functional['contract']['contract'] == 'passed' and functional['container_smoke']['correct'] == 4
        and functional['source_snapshot']['recursive_clone'] == 'passed', 'Missing cold clone checks')
for name, digest in functional['logs_sha256'].items():
    require(sha(name) == digest, 'Functional test log changed: ' + name)
license_report = document('reports/license-image-supplement.json')
require(license_report['image_runtime_files_equal'] and len(license_report['runtime_files_sha256']) == 41,
        'Missing image runtime equivalence')
require(git('service', 'rev-parse', 'HEAD') == functional['source_snapshot']['commits']['service'],
        'Service changed after functional tests')
require(git('eval', 'rev-parse', 'HEAD') == license_report['current_eval_commit'],
        'Evaluator changed after license supplement')
require(git('service', 'rev-parse', 'HEAD:src') == git('service', 'rev-parse', '910b3dd:src'),
        'Frozen service runtime changed')
equivalence = document('reports/evaluator-runtime-equivalence.json')
for version in equivalence['versions']:
    require(not git('eval', 'diff', '--name-only', version['commit'], 'HEAD', '--', *equivalence['reviewed_paths']),
            'Evaluated Answer/Judge runtime changed')
source = document('reports/source-contract-audit.json')
require(source['status'] == 'passed' and source['upstream_files_verified'] == 227, 'Missing source attribution audit')
upstream_files = document('service/baseline/upstream/source-manifest.json')['files']
require(len(upstream_files) == 227, 'Unexpected upstream file inventory')
for entry in upstream_files:
    require(sha('service/baseline/upstream/src/' + entry['path']) == entry['sha256'], 'Upstream original file changed')
require(sha('eval/contracts/contract.json') == sha('service/contracts/contract.json'), 'Contract copies differ')
reconstruction = document('reports/dataset-reconstruction.json')
for name, digest in reconstruction['reconstructed_hashes'].items():
    path = 'eval/' + name if name.startswith('configs/') else 'eval/.data/' + name
    require(sha(path) == digest, 'Reconstructed data changed: ' + path)
comparison = document('reports/development-final-comparison.json')
require(comparison['completed_runs'] == 32 and comparison['paired_comparisons'] == 44
        and sha(comparison['source']) == comparison['source_sha256'], 'Incomplete development comparison')
runtime = document('reports/runtime-bundles.json')
for archive in runtime['archives']:
    require(sha(archive['path']) == archive['sha256'] and (ROOT / archive['path']).stat().st_size == archive['bytes'],
            'Runtime archive changed')
require(document('reports/container-offline.json')['status'] == 'passed', 'Missing offline checks')
require(document('reports/embedding-import-verification.json')['passed'], 'Missing independent embedding import')
require(all(document('reports/command-boundary-audit.json')['checks'].values()), 'Missing scoped cleanup checks')
with urllib.request.urlopen('http://127.0.0.1:8088/health', timeout=10) as response:
    require(response.status == 200, 'Delivery service is not healthy')
container = json.loads(subprocess.check_output(
    ['docker', 'inspect', 'comp-memory-delivery-memory-service-1'], text=True))[0]
require(container['State']['Running'] and container['Image'] == functional['images']['service'],
        'Running deployment differs from tested service image')

# These are reviewed requirement/evidence mappings, not assertions of universal
# natural-language quality. Known failures remain explicitly present below.
requirements = [
    ('C01-C05', 'verified', ['reports/clean-clone-final-functional.json', 'service/tests/service.test.ts']),
    ('I01-I02,R02', 'verified', ['service/tests/service.test.ts']),
    ('W01-W03', 'verified', ['reports/service-tests-current.txt', 'service/tests/service.test.ts', 'service/tests/crash.test.ts']),
    ('L01-L05', 'bounded_behavior_verified', ['service/tests/lifecycle.test.ts', 'reports/semantic-acceptance.json']),
    ('F01-F06', 'bounded_behavior_verified', ['service/tests/lifecycle.test.ts', 'reports/container-offline.json']),
    ('S01-S02', 'bounded_behavior_verified', ['reports/semantic-acceptance.json']),
    ('T01-T02', 'known_answer_failures_disclosed', ['reports/semantic-acceptance.json', 'reports/pronoun-acceptance.json']),
    ('R01,R03', 'measured_quality_limitations_disclosed', ['reports/holdout-v2-locomo.json', 'reports/holdout-v2-memops.json']),
    ('D01-D02', 'verified_with_fallback_scope', ['reports/container-offline.json', 'reports/embedding-import-verification.json']),
    ('P01', 'measured', ['artifacts/final-performance/summary.json', 'reports/performance-v6-contended.json']),
    ('U0-U3,B0-B6,ablations', 'completed_no_superiority_claim', ['reports/development-final-comparison.json', 'reports/development-upstream-all-baselines.json']),
    ('1000_questions', 'completed_36_service_errors_disclosed', ['reports/holdout-v2-locomo.json', 'reports/holdout-v2-memops.json']),
    ('HTTP_only_no_gold', 'trace_and_source_audited', ['reports/completed-run-audit.json', 'reports/dataset-reconstruction.json', 'reports/evaluator-runtime-equivalence.json']),
    ('cost_and_resources', 'observed_scope_disclosed', ['reports/model-usage-and-resources.json', 'reports/storage-footprint.json']),
    ('M0-M4', 'implementation_and_configured_reproduction_complete', ['reports/REQUIREMENT-AUDIT.md', 'reports/DELIVERY-REPORT.md']),
    ('official_platform_score', 'conditional_unavailable', ['docs/EXPERIMENT-PROTOCOL.md']),
    ('final_archive', 'next_step_external_verification_required', ['scripts/package-delivery.py']),
]
rows = []
for identifier, status, paths in requirements:
    evidence.update(paths)
    rows.append({'requirement': identifier, 'status': status, 'evidence': paths})
report = {
    'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'status': 'ready_for_packaging',
    'scope': 'Pre-archive evidence readiness only. Final archive existence, readback hashes and recursive source clone must be verified separately. No universal quality or official-score claim.',
    'service_commit': git('service', 'rev-parse', 'HEAD'),
    'eval_commit': git('eval', 'rev-parse', 'HEAD'),
    'service_src_tree': git('service', 'rev-parse', 'HEAD:src'),
    'audit_script_sha256': sha('scripts/audit-delivery-readiness.py'),
    'primary_results': primary,
    'requirements': rows,
    'known_limitations': [
        '36 planned MemOps questions have no prediction after four target-binding write refusals.',
        'LoCoMo temporal, MemOps operation trace and state trajectory quality is weak.',
        'Two synthetic fixed-Answer failures remain: relative date conversion and pronoun resolution.',
        'Development comparisons do not demonstrate U3 superiority over original mem0.',
        'Answer diagnostic flags do not measure database deletion or retention rates.',
        'Official selector, Answer configuration and MemOps mapping are unavailable; local Qwen is quantized.',
        'Metal cold-start failure is retained; independent CPU embedding import was tested successfully.',
        'Cost and RSS reports cover explicitly observed subsets, not total billing or dedicated machine usage.',
    ],
    'evidence_sha256': {name: sha(name) for name in sorted(evidence)},
}
(ROOT / 'reports/delivery-readiness-audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'status': report['status'], 'requirements': len(rows), 'evidence_files': len(evidence)}))
