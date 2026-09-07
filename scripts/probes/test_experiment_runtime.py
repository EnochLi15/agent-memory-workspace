import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from experiment_runtime import ManagedRun, launch_detached, read_status, evaluation_schedule, run_benchmark_jobs


class ExperimentRuntimeTests(unittest.TestCase):
    def test_frozen_schedule_rejects_concurrency_override(self):
        spec = {'concurrency': 1, 'benchmark_execution': 'sequential'}
        self.assertEqual(evaluation_schedule(spec), (1, 'sequential'))
        self.assertEqual(evaluation_schedule({}), (3, 'parallel'))
        with self.assertRaisesRegex(ValueError, 'frozen'):
            evaluation_schedule(spec, 3)
        for value in (0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                evaluation_schedule({}, value)
        with self.assertRaises(ValueError):
            evaluation_schedule({'benchmark_execution': 'typo'})

    def test_sequential_jobs_wait_for_exit_and_do_not_continue_after_failure(self):
        for exit_code in (0, 7):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'runtime.json'
                children = []
                with ManagedRun(path) as run:
                    service = run.spawn('service', [sys.executable, '-c', 'import time;time.sleep(60)'])
                    def launch(name):
                        if children:
                            self.assertEqual(children[0].poll(), 0)
                        out = open(Path(directory) / (name + '.log'), 'w')
                        job = run.spawn(name, [sys.executable, '-c',
                                              f'import time;time.sleep(.05);raise SystemExit({exit_code})'], stdout=out)
                        children.append(job)
                        return name, job, out
                    if exit_code:
                        with self.assertRaisesRegex(RuntimeError, 'Evaluator process failed'):
                            run_benchmark_jobs(run, service, ['locomo', 'memops'], launch, 'sequential')
                    else:
                        run_benchmark_jobs(run, service, ['locomo', 'memops'], launch, 'sequential')
                    self.assertEqual(len(children), 1 if exit_code else 2)

    def test_concurrent_prepared_runners_cannot_overwrite_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'runtime.json'
            first, second = ManagedRun(path), ManagedRun(path)
            with first:
                saved = path.read_bytes()
                with self.assertRaises(FileExistsError):
                    with second:
                        self.fail('duplicate runner entered')
                self.assertEqual(path.read_bytes(), saved)

    def test_finished_and_failed_child_exit_codes_are_persisted(self):
        for code in (0, 7):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'runtime.json'
                with ManagedRun(path) as run:
                    child = run.spawn('eval', [sys.executable, '-c', f'raise SystemExit({code})'])
                    child.wait(timeout=5)
                    run.poll()
                state = read_status(path)
                self.assertEqual(state['status'], 'finished' if code == 0 else 'failed')
                self.assertEqual(state['children']['eval']['exit_code'], code)

    def test_exception_stops_owned_children_and_records_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'runtime.json'
            with self.assertRaisesRegex(RuntimeError, 'fixture'):
                with ManagedRun(path) as run:
                    child = run.spawn('service', [sys.executable, '-c', 'import time;time.sleep(60)'])
                    raise RuntimeError('fixture')
            self.assertIsNotNone(child.poll())
            self.assertEqual(read_status(path)['status'], 'failed')

    def test_detached_run_survives_launcher_exit_and_records_sigterm(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'runtime.json'
            script = root / 'worker.py'
            script.write_text('import time\nfrom pathlib import Path\nfrom experiment_runtime import ManagedRun\n'
                              f'with ManagedRun(Path({str(path)!r})) as run:\n'
                              ' while True:\n  run.poll()\n  time.sleep(.02)\n')
            env = {**os.environ, 'PYTHONPATH': str(SCRIPTS)}
            command = [sys.executable, '-c',
                       'from pathlib import Path;from experiment_runtime import launch_detached;'
                       f'launch_detached({[sys.executable, str(script)]!r},Path({str(path)!r}))']
            subprocess.run(command, env=env, timeout=5, check=True, stdout=subprocess.DEVNULL)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not path.exists():
                time.sleep(.02)
            state = read_status(path)
            pid = state['pid']
            try:
                self.assertTrue(state['alive'])
                with self.assertRaises(FileExistsError):
                    launch_detached([sys.executable, str(script)], path, env=env)
                os.kill(pid, signal.SIGTERM)
                while time.monotonic() < deadline and read_status(path)['status'] == 'running':
                    time.sleep(.02)
                self.assertEqual(read_status(path)['status'], 'interrupted')
                self.assertEqual(read_status(path)['signal'], signal.SIGTERM)
            finally:
                if read_status(path).get('alive'):
                    os.kill(pid, signal.SIGKILL)

    def test_missing_or_reused_pid_is_not_reported_as_running(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'runtime.json'
            saved = {'status': 'running', 'pid': os.getpid(), 'process_identity': 'not this process', 'children': {}}
            path.write_text(json.dumps(saved))
            self.assertEqual(read_status(path)['status'], 'interrupted')
            self.assertIsNone(read_status(path)['exit_code'])
            self.assertEqual(json.loads(path.read_text()), saved, 'inspection must preserve the original record')

    def test_launch_failure_is_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'runtime.json'
            with self.assertRaises(FileNotFoundError):
                launch_detached(['/nonexistent/v1-test-executable'], path)
            self.assertEqual(read_status(path)['status'], 'failed')
            self.assertFalse(read_status(path)['alive'])

    def test_sigkill_is_observed_without_fabricating_an_exit_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'runtime.json'
            env = {**os.environ, 'PYTHONPATH': str(SCRIPTS)}
            code = ('from pathlib import Path;import time;from experiment_runtime import ManagedRun;'
                    f'run=ManagedRun(Path({str(path)!r}));run.__enter__();time.sleep(60)')
            child = subprocess.Popen([sys.executable, '-c', code], env=env)
            try:
                deadline = time.monotonic() + 5
                while not path.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                saved = path.read_bytes()
                child.kill();child.wait(timeout=5)
                state = read_status(path)
                self.assertEqual(state['status'], 'interrupted')
                self.assertIsNone(state['exit_code'])
                self.assertIsNone(state['signal'])
                self.assertEqual(path.read_bytes(), saved)
            finally:
                if child.poll() is None:
                    child.kill();child.wait(timeout=5)

    def test_real_entry_records_startup_failure_and_status_does_not_require_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'candidate-runtime.json'
            command = [sys.executable, str(SCRIPTS / 'run-experiment.py'),
                       '--campaign', directory, '--profile', 'candidate']
            result = subprocess.run(command + ['--port', '19999', '--spec', directory + '/missing.json', '--detach'],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if path.exists() and read_status(path)['status'] == 'failed':
                    break
                time.sleep(.02)
            result = subprocess.run(command + ['--status'], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            state = json.loads(result.stdout)
            self.assertEqual(state['status'], 'failed')
            self.assertEqual(state['exit_code'], 1)
            self.assertEqual(state['error_type'], 'FileNotFoundError')


if __name__ == '__main__':
    unittest.main()
