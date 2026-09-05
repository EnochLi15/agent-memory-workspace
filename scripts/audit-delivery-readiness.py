"""Validate completed evidence before packaging; this does not validate a future archive."""
import datetime
import hashlib
import json
import pathlib
import subprocess
import urllib.request

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
    require(sha(row['source']) == row['source_sha256'], 'Usage log changed: ' + row['source'])
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

performance = document('artifacts/final-performance/summary.json')
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
