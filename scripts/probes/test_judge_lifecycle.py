import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from experiment_runtime import ManagedRun, read_status


class JudgeLifecycleTests(unittest.TestCase):
    def test_judge_death_is_detected_before_evaluation_and_stops_owned_siblings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'runtime.json'
            with self.assertRaisesRegex(RuntimeError, 'Required process judge exited'):
                with ManagedRun(path) as run:
                    judge = run.spawn('judge', [sys.executable, '-c', 'import time;time.sleep(60)'], required=True)
                    sibling = run.spawn('service', [sys.executable, '-c', 'import time;time.sleep(60)'])
                    judge.kill()
                    judge.wait(timeout=5)
                    run.poll()
            state = read_status(path)
            self.assertEqual(state['status'], 'failed')
            self.assertEqual(state['children']['judge']['exit_code'], -signal.SIGKILL)
            self.assertIsNotNone(sibling.poll())

    def test_adapter_binds_private_port_then_reports_identity_and_signal_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / 'ready.json'
            log = Path(directory) / 'judge.log'
            with log.open('w') as output:
                child = subprocess.Popen([sys.executable, str(ROOT / 'eval/scripts/ollama-judge-server.py'),
                                          '--port', '0', '--ready-file', str(ready)], stdout=output, stderr=output)
                try:
                    until = time.monotonic() + 3
                    while not ready.exists() and time.monotonic() < until:
                        time.sleep(.02)
                    self.assertTrue(ready.exists(), 'Adapter never published its bound endpoint')
                    data = json.loads(ready.read_text())
                    self.assertEqual(data['pid'], child.pid)
                    with urllib.request.urlopen(data['base_url'].removesuffix('/v1') + '/health', timeout=2) as response:
                        self.assertEqual(json.load(response)['pid'], child.pid)
                    child.terminate()
                    child.wait(timeout=5)
                finally:
                    if child.poll() is None:
                        child.kill(); child.wait(timeout=5)
            self.assertIn('"signal": 15', log.read_text())


if __name__ == '__main__':
    unittest.main()
