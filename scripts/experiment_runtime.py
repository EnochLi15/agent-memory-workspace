"""Process ownership and durable status for the existing experiment runner."""
import datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def process_identity(pid):
    try:
        value = subprocess.check_output(['ps', '-p', str(pid), '-o', 'stat=', '-o', 'lstart='],
                                        text=True, stderr=subprocess.DEVNULL).strip()
        state, started = value.split(None, 1)
        return None if state.startswith('Z') else started.strip()
    except (subprocess.SubprocessError, ValueError):
        return None


def write_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w') as output:
        os.chmod(temporary, 0o600)
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write('\n')
    temporary.replace(path)


def read_status(path):
    path = Path(path)
    saved = path if path.exists() else path.with_suffix('.launch.json')
    state = json.loads(saved.read_text())
    identity = state.get('process_identity')
    state['alive'] = bool(identity and process_identity(state['pid']) == identity)
    for child in state.get('children', {}).values():
        child['alive'] = bool(child.get('process_identity') and
                              process_identity(child['pid']) == child['process_identity'])
    if state['status'] in ('running', 'launching') and not state['alive']:
        state.update(status='interrupted', exit_code=None, signal=None,
                     observation='Runner is absent or its PID was reused; exit cause is unknown.')
    return state


def launch_detached(command, path, **kwargs):
    """Detach once, with all standard streams closed or redirected to files."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError('Preserve the existing run; do not relaunch it')
    with path.with_suffix('.launch.json').open('x') as launch:
        os.chmod(launch.name, 0o600)
        with path.with_suffix('.log').open('x') as log:
            os.chmod(log.name, 0o600)
            try:
                child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                         stderr=subprocess.STDOUT, start_new_session=True, **kwargs)
            except OSError as error:
                json.dump({'status': 'failed', 'pid': None, 'process_identity': None,
                           'started_at': now(), 'finished_at': now(), 'exit_code': None,
                           'error_type': type(error).__name__}, launch, indent=2)
                raise
        state = {'status': 'launching', 'pid': child.pid, 'process_identity': process_identity(child.pid),
                 'started_at': now(), 'log': str(path.with_suffix('.log'))}
        json.dump(state, launch, indent=2)
    return state


class RunInterrupted(BaseException):
    def __init__(self, signum):
        self.signum = signum


class ManagedRun:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            raise FileExistsError('Preserve the existing runtime record')
        self.processes = {}
        self.handlers = {}
        self.last_tick = time.time()
        self.state = {'protocol': 'experiment-runtime-v1', 'status': 'running', 'pid': os.getpid(),
                      'process_identity': process_identity(os.getpid()), 'started_at': now(),
                      'updated_at': now(), 'children': {}, 'monitor_gaps': []}

    def __enter__(self):
        # Claim ownership atomically, including concurrent foreground launches.
        with self.path.with_suffix('.owner').open('x') as claim:
            os.chmod(claim.name, 0o600)
            claim.write(str(os.getpid()))
        for sig in (signal.SIGTERM, signal.SIGINT):
            self.handlers[sig] = signal.signal(sig, self.interrupt)
        write_json(self.path, self.state)
        return self

    def interrupt(self, signum, _frame):
        raise RunInterrupted(signum)

    def spawn(self, role, command, **kwargs):
        if role in self.processes:
            raise ValueError('A managed role cannot be launched twice')
        child = subprocess.Popen(command, **kwargs)
        self.processes[role] = child
        self.state['children'][role] = {'pid': child.pid, 'process_identity': process_identity(child.pid),
                                        'started_at': now(), 'exit_code': None}
        self.poll()
        return child

    def poll(self):
        tick = time.time()
        if tick - self.last_tick > 30:
            self.state['monitor_gaps'].append({'at': now(), 'seconds': round(tick - self.last_tick, 3),
                                                'cause': 'unknown; may include host suspension'})
        self.last_tick = tick
        for role, child in self.processes.items():
            code = child.poll()
            row = self.state['children'][role]
            if code is not None and row['exit_code'] is None:
                row.update(exit_code=code, finished_at=now())
        self.state['updated_at'] = now()
        write_json(self.path, self.state)

    def __exit__(self, kind, error, _traceback):
        # Terminate only Popen handles owned by this run; never signal saved PIDs.
        for role, child in self.processes.items():
            if child.poll() is None:
                self.state['children'][role]['stopped_by_runner'] = True
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        self.poll()
        failed = any(r['exit_code'] != 0 and not r.get('stopped_by_runner')
                     for r in self.state['children'].values())
        self.state.update(status='interrupted' if isinstance(error, RunInterrupted) else
                          'failed' if kind or failed else 'finished', finished_at=now())
        self.state['exit_code'] = (128 + error.signum if isinstance(error, RunInterrupted) else
                                  (error.code if isinstance(error, SystemExit) and isinstance(error.code, int)
                                   else 1) if kind else 0)
        if error:
            self.state['error_type'] = type(error).__name__
        if isinstance(error, RunInterrupted):
            self.state['signal'] = error.signum
        write_json(self.path, self.state)
        for sig, handler in self.handlers.items():
            signal.signal(sig, handler)
        return False
