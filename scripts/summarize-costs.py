"""Summarize recorded model usage and host samples; never estimate a bill."""
import argparse
import collections
import datetime
import hashlib
import json
import pathlib
import statistics

ROOT = pathlib.Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--require-complete', action='store_true')
args = parser.parse_args()


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stats(values):
    values = sorted(values)
    if not values:
        return {'n': 0}
    return {'n': len(values), 'mean': statistics.mean(values),
            'p95': values[min(len(values) - 1, int(len(values) * .95))], 'max': values[-1]}


profiles = ['U3', 'B2', 'B0', 'B1', 'U2', 'B3', 'B4', 'B5', 'B6',
            'no_lifecycle', 'no_raw', 'no_time', 'no_hop', 'no_rerank']
groups = [('dev-v6', p) for p in profiles] + [('baseline-v7', p) for p in ['U0', 'U1']]
groups += [('holdout-v2', 'U3')]
pending = []
for campaign, profile in groups:
    for benchmark in ['locomo', 'memops']:
        name = f'{campaign}-{profile}-{benchmark}'
        path = ROOT / 'eval/artifacts' / name / 'manifest.json'
        if not path.exists() or json.loads(path.read_text())['status'] != 'finished':
            pending.append(name)
if args.require_complete and pending:
    raise SystemExit('Wait for complete runs before final usage accounting: ' + json.dumps(pending))

summaries = []
for campaign, profile in groups:
    path = ROOT / 'artifacts' / campaign / f'{profile}-model-calls.jsonl'
    record = {'campaign': campaign, 'profile': profile}
    if path.exists():
        rows = read_rows(path)
        generation = [r for r in rows if r['kind'] == 'generation']
        embeddings = [r for r in rows if r['kind'] == 'embedding']
        usage = collections.Counter()
        cached = 0
        for row in generation:
            u = row.get('usage') or {}
            for key in ['prompt_tokens', 'completion_tokens', 'total_tokens']:
                usage[key] += u.get(key, 0)
            cached += (u.get('prompt_tokens_details') or {}).get('cached_tokens', 0)
        record.update(source=str(path.relative_to(ROOT)), source_sha256=sha(path),
                      first_at=rows[0]['at'] if rows else None, last_at=rows[-1]['at'] if rows else None,
                      generation={'recorded_attempts': len(generation),
                                  'outcomes': dict(collections.Counter(r['outcome'] for r in generation)),
                                  'purposes': dict(collections.Counter(r.get('purpose', 'unrecorded') for r in generation)),
                                  'provider_usage': dict(usage), 'cached_prompt_tokens': cached,
                                  'attempts_with_usage': sum(isinstance(r.get('usage'), dict) for r in generation),
                                  'elapsed_ms': stats([r['elapsed_ms'] for r in generation])},
                      embedding={'recorded_calls': len(embeddings),
                                 'outcomes': dict(collections.Counter(r['outcome'] for r in embeddings)),
                                 'vectors': sum(r.get('count', 0) for r in embeddings),
                                 'reported_prompt_eval_count': sum(r.get('prompt_eval_count') or 0 for r in embeddings),
                                 'elapsed_ms': stats([r['elapsed_ms'] for r in embeddings])})
    else:
        record['model_call_accounting'] = 'N/A: no model audit file; absence does not establish zero calls.'
    service_log = ROOT / 'artifacts' / campaign / f'{profile}-service.log'
    if service_log.exists():
        events = []
        for line in service_log.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get('event') == 'degraded':
                events.append(row)
        record['successful_add_degradation_events'] = len(events)
        record['recorded_degradation_reasons'] = dict(collections.Counter(
            reason for event in events for reason in event.get('reasons', [])))
        record['service_log_sha256'] = sha(service_log)
    summaries.append(record)

posthoc = []
for name in ['dev-v6-B1-memops', 'dev-v6-U3-memops', 'baseline-v7-U0-memops',
             'baseline-v7-U1-memops', 'holdout-v2-U3-memops']:
    directory = ROOT / 'eval/artifacts' / name / 'upstream-memops-diagnostics'
    calls = directory / 'calls.jsonl'
    entry = {'run_id': name, 'complete': (directory / 'summary.json').exists()}
    if calls.exists():
        observed = read_rows(calls)
        usage = collections.Counter()
        cached = 0
        for row in observed:
            u = row.get('usage') or {}
            for key in ['prompt_tokens', 'completion_tokens', 'total_tokens']:
                usage[key] += u.get(key, 0)
            cached += (u.get('prompt_tokens_details') or {}).get('cached_tokens', 0)
        entry.update(source=str(calls.relative_to(ROOT)), source_sha256=sha(calls),
                     recorded_calls=len(observed), provider_usage=dict(usage),
                     cached_prompt_tokens=cached,
                     elapsed_ms=stats([row['elapsed_ms'] for row in observed]))
    else:
        entry['availability'] = 'N/A: no saved call log yet'
    posthoc.append(entry)

resource_path = ROOT / 'artifacts/holdout-v2/resources.jsonl'
resources = read_rows(resource_path)
resource_summary = {'source_sha256': sha(resource_path), 'samples': len(resources),
                    'first_at': resources[0]['at'], 'last_at': resources[-1]['at'],
                    'experiment_process_rss_kib': stats([r['experiments']['rss_kib'] for r in resources]),
                    'experiment_cpu_percent_sum': stats([r['experiments']['cpu_percent_sum'] for r in resources]),
                    'ollama_process_rss_kib': stats([r['ollama']['rss_kib'] for r in resources]),
                    'ollama_cpu_percent_sum': stats([r['ollama']['cpu_percent_sum'] for r in resources]),
                    'scope': '15-second process samples across concurrent experiments; not isolated per-campaign '
                             'memory, GPU utilization, or integrated CPU time. Shared pages can be counted twice. '
                             'User applications and Docker VM are not attributed; sampling began after run start.'}
report = {'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'complete_evaluation_runs': not pending, 'pending': pending,
          'script_sha256': sha(pathlib.Path(__file__)), 'service_model_usage': summaries,
          'saved_memops_posthoc_usage': posthoc,
          'complete_memops_posthoc_logs': all(row['complete'] for row in posthoc),
          'host_sampling': resource_summary,
          'scope': 'Service-side observations plus saved upstream MemOps posthoc calls. Ordinary '
                   'Answer/Judge, hypothesis-generator and original baseline service billing are not logged. '
                   'Posthoc requests that fail before writing a call log can be absent. '
                   'Failed generation calls may lack usage; failed embedding calls '
                   'are not recorded by this hook. Contract probes on separate tenants can be included in '
                   'the same process log. Cached tokens are a subset of prompt tokens. No monetary estimate.'}
(ROOT / 'reports/model-usage-and-resources.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({'service_profiles': len(summaries), 'complete': not pending, 'resource_samples': len(resources)}))
