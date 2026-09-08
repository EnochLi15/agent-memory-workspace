"""Own one local Judge per experiment; private port, model preflight, no rejudging."""
import json
import subprocess
import time
import urllib.request


def start_local_judge(runtime, files, root, campaign, profile, env):
    ready = campaign / (profile + '-judge-ready.json')
    if ready.exists():
        raise FileExistsError('Preserve the previous judge endpoint record')
    output = files.enter_context(open(campaign / (profile + '-judge.log'), 'x'))
    python = str(root / 'eval/.venv/bin/python')
    child = runtime.spawn('judge', [python, str(root / 'eval/scripts/ollama-judge-server.py'),
                                   '--port', '0', '--ready-file', str(ready)],
                          required=True, env=env, stdin=subprocess.DEVNULL,
                          stdout=output, stderr=subprocess.STDOUT)
    until = time.monotonic() + 10
    while not ready.exists():
        runtime.poll()
        if time.monotonic() > until:
            raise RuntimeError('Owned Judge did not publish its bound endpoint')
        time.sleep(.05)
    data = json.loads(ready.read_text())
    if data['pid'] != child.pid:
        raise RuntimeError('Judge endpoint ownership mismatch')
    with urllib.request.urlopen(data['base_url'].removesuffix('/v1') + '/health', timeout=3) as response:
        if json.load(response)['pid'] != child.pid:
            raise RuntimeError('Judge health identity mismatch')
    # Exercise the unmodified upstream prompt and bridge before any ingestion.
    probe_env = {**env, 'EVALUATOR_MODEL':'qwen3:14b', 'EVALUATOR_API_BASE':data['base_url'], 'EVALUATOR_API_KEY':'local'}
    probe_log = files.enter_context(open(campaign / (profile + '-judge-preflight.log'), 'x+'))
    probe = runtime.spawn('judge-preflight', [python, str(root / 'eval/python/judge_bridge.py')],
                          env=probe_env, stdin=subprocess.PIPE, stdout=probe_log, stderr=subprocess.STDOUT, text=True)
    probe.stdin.write(json.dumps({'question':'What color is stated: blue?', 'gold':['Blue'], 'answer':'Blue'}))
    probe.stdin.close()
    until = time.monotonic() + 190
    while probe.poll() is None:
        runtime.poll()
        if time.monotonic() > until:
            raise RuntimeError('Judge model preflight exceeded its deadline')
        time.sleep(.1)
    runtime.poll()
    if probe.returncode:
        raise RuntimeError('Judge model preflight failed; inspect its saved log')
    probe_log.seek(0)
    try:
        result = json.loads(probe_log.read())
        if result.get('score') != 1:
            raise ValueError('unexpected score')
    except (ValueError, TypeError):
        raise RuntimeError('Judge model preflight did not produce the expected verdict')
    return data['base_url']
