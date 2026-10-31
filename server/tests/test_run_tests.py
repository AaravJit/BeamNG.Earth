"""The parallel runner's durable manifest, using tiny isolated unittest modules in real child processes."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import run_tests  # noqa: E402


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.logs = self.root / "logs"
        self.patch = mock.patch.multiple(run_tests, TESTS=str(self.root), HISTORY=str(self.root / "times.json"))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def module(self, name, body):
        (self.root / (name + ".py")).write_text("import unittest\n" + body, encoding="utf-8")
        return name

    def runner(self, modules, timeout=5, par=2):
        runner = run_tests.Runner(modules, par, timeout, str(self.logs), {})
        runner.say = mock.Mock()
        return runner

    def manifest(self):
        return json.loads((self.logs / "results.json").read_text())

    def test_aggregation_including_subtests_and_expected_outcomes(self):
        self.module("test_good", "class T(unittest.TestCase):\n def test_ok(self): pass\n")
        self.module("test_mixed", '''class T(unittest.TestCase):
 def test_ok(self): pass
 def test_failure(self): self.fail('failure')
 def test_error(self): raise RuntimeError('error')
 @unittest.skip('skip')
 def test_skip(self): pass
 @unittest.expectedFailure
 def test_expected(self): self.fail('expected')
 @unittest.expectedFailure
 def test_unexpected(self): pass
 def test_subtests(self):
  for x in range(2):
   with self.subTest(x=x): self.fail('subtest')
''')
        self.assertEqual(self.runner(["test_good", "test_mixed"]).all(), 1)
        data = self.manifest()
        self.assertTrue(data["complete"])
        self.assertFalse(data["success"])
        self.assertEqual(data["counts"], dict(tests_run=8, passed=2, failures=3, errors=1, skipped=1,
                                             expected_failures=1, unexpected_successes=1))
        self.assertEqual([m["status"] for m in data["modules"]], ["passed", "failed"])
        for m in data["modules"]:
            self.assertGreater(m["duration_seconds"], 0)
            self.assertEqual(m["timeout_seconds"], 5)
            self.assertFalse(m["timed_out"])
            self.assertTrue(Path(m["log"]).is_absolute())
            self.assertTrue(Path(m["log"]).exists())

    def test_success_and_custom_manifest_path(self):
        name = self.module("test_ok", "class T(unittest.TestCase):\n def test_ok(self): pass\n")
        runner = self.runner([name])
        runner.results_path = str(self.root / "elsewhere" / "manifest.json")
        self.assertEqual(runner.all(), 0)
        data = json.loads(Path(runner.results_path).read_text())
        self.assertTrue(data["success"])
        self.assertTrue(data["complete"])
        self.assertEqual(data["counts"]["passed"], 1)
        self.assertEqual(data["modules"][0]["returncode"], 0)

    def test_importable_empty_module_is_unsuccessful(self):
        empty = self.module("test_empty", "# Importable, but contains no tests.\n")
        good = self.module("test_good", "class T(unittest.TestCase):\n def test_ok(self): pass\n")
        skipped = self.module("test_skipped", "class T(unittest.TestCase):\n @unittest.skip('skip')\n def test_skip(self): pass\n")
        for others in ([], [good, skipped]):
            with self.subTest(others=others):
                self.assertEqual(self.runner([empty, *others]).all(), 1)
                data = self.manifest()
                self.assertFalse(data["success"])
                self.assertTrue(data["complete"], "unittest completed, but discovered no tests")
                module = data["modules"][0]
                self.assertEqual(module["status"], "no_tests")
                self.assertEqual(module["counts"], {k: 0 for k in run_tests.COUNTS})
                self.assertEqual(module["returncode"], 0, "a zero child exit must not mask an empty suite")
                self.assertEqual([m["status"] for m in data["modules"][1:]], ["passed"] * len(others))

    def test_all_skipped_module_remains_successful(self):
        name = self.module("test_skipped", "class T(unittest.TestCase):\n @unittest.skip('skip')\n def test_skip(self): pass\n")
        self.assertEqual(self.runner([name]).all(), 0)
        data = self.manifest()
        self.assertTrue(data["success"])
        self.assertEqual(data["modules"][0]["status"], "passed")
        self.assertEqual(data["counts"]["tests_run"], 1)
        self.assertEqual(data["counts"]["passed"], 0)
        self.assertEqual(data["counts"]["skipped"], 1)

    def test_timeout_keeps_partial_counts_and_other_modules(self):
        slow = self.module("test_slow", '''import time
class T(unittest.TestCase):
 def test_00_pass(self): pass
 def test_10_hang(self): time.sleep(60)
''')
        good = self.module("test_good", "class T(unittest.TestCase):\n def test_ok(self): pass\n")
        runner = self.runner([slow, good], timeout=2)
        self.assertEqual(runner.all(), 1)
        data = self.manifest()
        self.assertFalse(data["success"])
        self.assertFalse(data["complete"])
        self.assertEqual([m["status"] for m in data["modules"]], ["timed_out", "passed"])
        partial = data["modules"][0]
        self.assertTrue(partial["timed_out"])
        self.assertEqual(partial["counts"]["passed"], 1)
        self.assertEqual(partial["counts"]["tests_run"], 2)
        self.assertNotEqual(partial["returncode"], 0)
        self.assertNotIn(slow, runner.hist, "timeouts must not replace ETA history")
        self.assertIsNotNone(runner.state[slow]["proc"].poll())

    def test_zero_exit_without_result_cannot_reuse_stale_success(self):
        name = self.module("test_exit", "import os\nos._exit(0)\n")
        self.logs.mkdir()
        (self.logs / (name + ".result.json")).write_text(json.dumps(dict(complete=True,
            **{k: 0 for k in run_tests.COUNTS})))
        self.assertEqual(self.runner([name]).all(), 1)
        data = self.manifest()
        self.assertFalse(data["success"])
        self.assertFalse(data["complete"])
        self.assertEqual(data["modules"][0]["status"], "incomplete")
        self.assertEqual(data["modules"][0]["returncode"], 0)

    @unittest.skipIf(os.name == "nt", "POSIX process group survives its leader")
    def test_timeout_kills_descendant_holding_pipe_after_parent_exit(self):
        name = self.module("test_descendant", '''import os, subprocess, sys
subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
os._exit(0)
''')
        runner = self.runner([name], timeout=2)
        self.assertEqual(runner.all(), 1)
        data = self.manifest()
        self.assertEqual(data["modules"][0]["status"], "timed_out")
        self.assertEqual(data["modules"][0]["returncode"], 0)
        self.assertFalse(data["success"])
        self.assertLess(data["duration_seconds"], 10)

    def test_spawn_error_records_every_module(self):
        runner = self.runner(["test_a", "test_b"])
        with mock.patch.object(run_tests.subprocess, "Popen", side_effect=OSError("cannot launch")):
            self.assertEqual(runner.all(), 1)
        data = self.manifest()
        self.assertEqual([m["status"] for m in data["modules"]], ["error", "error"])
        self.assertTrue(all(m["error"] == "cannot launch" for m in data["modules"]))
        self.assertFalse(data["complete"])
        self.assertFalse(data["success"])

    def test_exit_after_some_tests_keeps_partial_counts(self):
        name = self.module("test_partial", '''import os
class T(unittest.TestCase):
 def test_00_pass(self): pass
 def test_10_exit(self): os._exit(0)
 def test_20_not_reached(self): pass
''')
        self.assertEqual(self.runner([name]).all(), 1)
        data = self.manifest()
        self.assertEqual(data["modules"][0]["status"], "incomplete")
        self.assertEqual(data["counts"]["passed"], 1)
        self.assertEqual(data["counts"]["tests_run"], 2)
        self.assertFalse(data["success"])

    def test_initial_and_partial_manifest_are_never_successful(self):
        runner = self.runner(["test_a", "test_b"])
        runner.manifest()
        data = self.manifest()
        self.assertEqual([m["status"] for m in data["modules"]], ["queued", "queued"])
        self.assertFalse(data["success"])
        runner.result("test_a", status="passed", complete=True, counts=dict(tests_run=1, passed=1,
                      failures=0, errors=0, skipped=0, expected_failures=0, unexpected_successes=0))
        data = runner.manifest("finished")
        self.assertFalse(data["success"])
        self.assertFalse(data["complete"])
        self.assertEqual(data["counts"]["passed"], 1)

    def test_empty_selection_is_not_success(self):
        self.assertEqual(self.runner([]).all(), 1)
        self.assertFalse(self.manifest()["success"])

    @unittest.skipIf(os.name == "nt", "POSIX signals; Windows timeout coverage runs above")
    def test_sigterm_stops_running_and_records_unstarted_modules(self):
        self.module("test_hang", '''import time
class T(unittest.TestCase):
 def test_00_pass(self): pass
 def test_10_hang(self): time.sleep(60)
''')
        self.module("test_queued", "class T(unittest.TestCase):\n def test_ok(self): pass\n")
        # Launch the real CLI, redirecting discovery/history to this isolated fixture tree.
        code = (f"import sys; sys.path.insert(0, {str(Path(run_tests.__file__).parent)!r}); import run_tests; "
                f"run_tests.TESTS={str(self.root)!r}; run_tests.HISTORY={str(self.root / 'times.json')!r}; "
                "sys.exit(run_tests.main())")
        p = subprocess.Popen([sys.executable, "-c", code, "test_hang", "test_queued", "--par", "1",
                              "--timeout", "30", "--logs", str(self.logs)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 10
            checkpoint = self.logs / "test_hang.result.json"
            while time.monotonic() < deadline:
                if checkpoint.exists() and json.loads(checkpoint.read_text())["passed"] == 1:
                    break
                time.sleep(0.01)
            self.assertTrue(checkpoint.exists(), "first test must complete before interruption")
            p.send_signal(signal.SIGTERM)
            self.assertEqual(p.wait(timeout=10), 130)
            data = self.manifest()
            self.assertEqual(data["status"], "interrupted")
            self.assertFalse(data["success"])
            self.assertFalse(data["complete"])
            self.assertEqual([m["status"] for m in data["modules"]], ["interrupted", "not_run"])
            self.assertEqual(data["counts"]["passed"], 1)
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()


if __name__ == "__main__":
    unittest.main()
