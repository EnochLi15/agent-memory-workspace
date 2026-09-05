"""Run the final HTTP performance measurements after the finite evaluation jobs finish."""
import datetime
import hashlib
import json
import os
import pathlib
import shutil
import socket
import subprocess
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/final-performance'
PROFILES = ['U3', 'B2', 'B0', 'B1', 'U2', 'B3', 'B4', 'B5', 'B6',
            'no_lifecycle', 'no_raw', 'no_time', 'no_hop', 'no_rerank']
RUNS = [f'dev-v6-{p}-{b}' for p in PROFILES for b in ['locomo', 'memops']]
RUNS += [f'baseline-v7-{p}-{b}' for p in ['U0', 'U1'] for b in ['locomo', 'memops']]
RUNS += [f'holdout-v2-U3-{b}' for b in ['locomo', 'memops']]


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def event(value):
    value = {'at': now(), **value}
    with (OUT / 'workflow.jsonl').open('a') as stream:
        stream.write(json.dumps(value) + '\n')
    print(json.dumps(value), flush=True)


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def model_state():
    with urllib.request.urlopen('http://127.0.0.1:11434/api/ps', timeout=10) as response:
        return [{k: m.get(k) for k in ['name', 'digest', 'size', 'size_vram', 'expires_at']}
                for m in json.load(response).get('models', [])]


def wait_for_evaluation():
    started = time.monotonic()
    last = None
    while time.monotonic() - started < 28800:
        pending = []
        for run in RUNS:
            manifest = ROOT / 'eval/artifacts' / run / 'manifest.json'
            if not manifest.exists() or json.loads(manifest.read_text())['status'] != 'finished':
                pending.append(run)
        workflow = ROOT / 'artifacts/report-workflow.json'
        reports_ready = False
        if workflow.exists():
            results = json.loads(workflow.read_text())['results']
            if any(r['status'] == 'error' for r in results):
                raise RuntimeError('Posthoc reporting failed; resolve it before final performance measurement')
            reports_ready = len(results) == 3 and all(r['status'] == 'complete' for r in results)
        commands = subprocess.check_output(['ps', '-axo', 'command='], text=True).splitlines()
        busy = any(any(s in line for s in ['scripts/run-experiment.py ', 'scripts/run-ablation-suite.py ',
                                          'scripts/memops-diagnostic.py ', 'scripts/diagnose-answers.mjs '])
                   for line in commands)
        state = (len(pending), reports_ready, busy)
        if state != last:
            event({'stage': 'waiting', 'unfinished_runs': len(pending),
                   'reports_ready': reports_ready, 'evaluation_processes_live': busy})
            last = state
        if not pending and reports_ready and not busy:
            # The existing finite Judge residency manager unloads our local Judge.
            # If another process keeps it resident, wait rather than evicting it.
            if any(m['name'].startswith('qwen3:14b') for m in model_state()):
                time.sleep(15)
                continue
            return
        time.sleep(15)
    raise RuntimeError('Evaluation dependencies did not finish within eight hours')


def snapshot():
    rows = subprocess.check_output(['ps', '-axo', '%cpu=,rss='], text=True).splitlines()
    pairs = [list(map(float, row.split())) for row in rows if len(row.split()) == 2]
    return {'at': now(), 'load_average': list(os.getloadavg()),
            'host_process_cpu_percent_sum': sum(p[0] for p in pairs),
            'host_process_rss_kib_sum': sum(p[1] for p in pairs),
            'swap': subprocess.check_output(['sysctl', '-n', 'vm.swapusage'], text=True).strip(),
            'resident_ollama_models': model_state(),
            'scope': 'Whole-host snapshot; user applications remain running and shared RSS may be counted more than once.'}


def quantiles(values):
    values = sorted(values)
    return {'n': len(values), **{name: values[min(len(values)-1, int(len(values)*q))] if values else None
                                for name, q in [('p50', .5), ('p95', .95), ('p99', .99), ('max', 1)]}}


def measure(name, instrumented):
    directory = OUT / name
    directory.mkdir()
    data = ROOT / 'service/.data' / ('final-performance-' + name)
    if data.exists():
        raise RuntimeError('Preserve prior benchmark database: ' + str(data))
    # Reject an occupied port, so a health check cannot accidentally select an old service.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 8086))
    env = {k: v for k, v in os.environ.items() if not k.startswith(('MEMORY_', 'PERF_'))}
    env.update({'MEMORY_MODE': 'offline', 'MEMORY_DATA_DIR': str(data),
                'HOST': '127.0.0.1', 'PORT': '8086', 'PERF_BASE_URL': 'http://127.0.0.1:8086',
                'PERF_FACTS': '5000'})
    args = [shutil.which('node')]
    if instrumented:
        env['MEMORY_PERF_AUDIT_DIR'] = str(directory / 'instrumentation')
        args += ['--import', './scripts/performance-preload.mjs']
    args += ['dist/server.js']
    before = snapshot()
    event({'stage': 'measurement_start', 'name': name})
    with (directory / 'service.log').open('w') as log:
        service = subprocess.Popen(args, cwd=ROOT / 'service', env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 90
            while True:
                if service.poll() is not None:
                    raise RuntimeError('Benchmark service exited before readiness')
                try:
                    with urllib.request.urlopen(env['PERF_BASE_URL'] + '/health', timeout=2) as response:
                        if response.status == 200:
                            break
                except (OSError, TimeoutError):
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError('Benchmark readiness deadline')
                time.sleep(.2)
            with (directory / 'client.log').open('w') as client:
                subprocess.run([shutil.which('node'), 'scripts/performance.mjs'], cwd=ROOT / 'eval',
                               env=env, stdout=client, stderr=subprocess.STDOUT, check=True, timeout=3600)
            shutil.copy2(ROOT / 'eval/artifacts/performance/metrics.json', directory / 'metrics.json')
        finally:
            service.terminate()
            try:
                service.wait(timeout=15)
            except subprocess.TimeoutExpired:
                service.kill()
                service.wait()
    report = {'started_host_state': before, 'finished_host_state': snapshot(),
              'metrics': json.loads((directory / 'metrics.json').read_text()),
              'database_files_bytes': sum(p.stat().st_size for p in data.rglob('*') if p.is_file()),
              'instrumented': instrumented, 'source_commit': git('-C', 'service', 'rev-parse', 'HEAD'),
              'src_tree': git('-C', 'service', 'rev-parse', 'HEAD:src'),
              'performance_script_sha256': digest(ROOT / 'eval/scripts/performance.mjs'),
              'preload_script_sha256': digest(ROOT / 'service/scripts/performance-preload.mjs') if instrumented else None,
              'node': subprocess.check_output([shutil.which('node'), '--version'], text=True).strip(),
              'scope': 'Fresh offline service, 5000 facts, real HTTP; no other experiment runs. User applications untouched. Instrumented latency includes diagnostic logging overhead.'}
    if instrumented:
        rows = [json.loads(line) for p in (directory / 'instrumentation').glob('*.jsonl')
                for line in p.read_text().splitlines()]
        dispatch = [r for r in rows if r['kind'] == 'worker_dispatch']
        loops = [r for r in rows if r['kind'] == 'main_event_loop_delay']
        if not dispatch or not loops:
            raise RuntimeError('Missing Worker or event-loop instrumentation')
        report['worker_by_method'] = {method: {
            'dispatch_wait_ms': quantiles([r['dispatch_wait_ms'] for r in dispatch if r['method'] == method]),
            'handler_ms': quantiles([r['handler_ms'] for r in dispatch if r['method'] == method])}
            for method in sorted({r['method'] for r in dispatch})}
        report['event_loop_windows'] = {'count': len(loops), 'window_ms': 1000, 'resolution_ms': 10,
            'largest_window_p99_ms': max(r['p99_ms'] for r in loops),
            'largest_observed_delay_ms': max(r['max_ms'] for r in loops),
            'scope': 'Largest individual one-second-window statistics, not pooled or averaged quantiles.'}
    (directory / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    event({'stage': 'measurement_complete', 'name': name})
    return report


if __name__ == '__main__':
    OUT.mkdir(exist_ok=False)
    event({'stage': 'started', 'dependencies': len(RUNS), 'script_sha256': digest(pathlib.Path(__file__))})
    try:
        wait_for_evaluation()
        if git('-C', 'service', 'rev-parse', 'HEAD:src') != git('-C', 'service', 'rev-parse', '910b3dd:src'):
            raise RuntimeError('Production source differs from the frozen evaluated candidate')
        reports = {name: measure(name, flag) for name, flag in [('primary', False), ('instrumented', True)]}
        (OUT / 'summary.json').write_text(json.dumps({'status': 'complete', 'finished_at': now(), 'measurements': reports}, indent=2) + '\n')
        event({'stage': 'complete'})
    except Exception as error:
        event({'stage': 'error', 'error': str(error)})
        raise
