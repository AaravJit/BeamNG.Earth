#!/usr/bin/env python3
"""The server tests, each module in its own process, several at a time, with live progress and an ETA.

  python3 tools/run_tests.py                     every server/tests/test_*.py
  python3 tools/run_tests.py test_roads test_dem  just these modules

  --par N        modules at a time (default 6)
  --timeout S    a module still running after S seconds is killed with its child processes (default 1500);
                 shortly before, faulthandler prints every thread's stack (where it hangs)
  --logs DIR     one live log per module (unittest -v): tail -f DIR/<module>.log (default build/test-logs)
  --results FILE JSON manifest (default <logs>/results.json), updated atomically throughout the run

Prints a line for every test that fails, errors, is skipped or takes over 5 s, a status line every 30 s (modules
done, the ETA, what is running and its current test) and a summary per module. The ETA comes from the earlier runs'
times (build/test_times.json); the longest modules start first. Exit status 1 when a module did not pass.
BNGE_SKIP_DRIVE=1 skips the drive test, as with unittest discover.
The manifest includes every selected module, counts (including expected failures and unexpected successes),
duration, timeout, exit code and absolute log path. Counts on incomplete modules are partial. A successful run
requires a completed unittest result from every module; an interrupted runner leaves success=false even if killed
before cleanup. Per-module .result.json files beside the logs checkpoint counts after each test.
"""
import argparse
import concurrent.futures as cf
import glob
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
TESTS = os.path.join(REPO_ROOT, "server", "tests")
BUILD = os.path.join(REPO_ROOT, "build")
HISTORY = os.path.join(BUILD, "test_times.json")
DEFAULT_S = 30.0        # a module never timed before
SLOW_S = 5.0            # a passing test this slow gets its own line
STATUS_S = 30.0
# unittest -v: "test_x (module.Class.test_x) ... ok"; the verdict may come lines later (the test's own output)
START = re.compile(r"^(test\w*) \(([\w.]+)\)")
VERDICT = re.compile(r"\b(ok|FAIL|ERROR|skipped.*|expected failure|unexpected success)$")
COUNTS = ("tests_run", "passed", "failures", "errors", "skipped", "expected_failures", "unexpected_successes")

# Structured counts avoid guessing from verbose output (subtests and test-owned output can look like verdicts).
CHILD = r'''
import faulthandler, json, os, sys, unittest
module, destination, timeout = sys.argv[1:]
faulthandler.dump_traceback_later(int(timeout))
class Result(unittest.TextTestResult):
    passed = 0
    def startTest(self, test):
        super().startTest(test)
        self.checkpoint()
    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed += 1
    def checkpoint(self, complete=False):
        data = dict(complete=complete, tests_run=self.testsRun, passed=self.passed,
                    failures=len(self.failures), errors=len(self.errors), skipped=len(self.skipped),
                    expected_failures=len(self.expectedFailures), unexpected_successes=len(self.unexpectedSuccesses))
        with open(destination + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(data, f)
        os.replace(destination + '.tmp', destination)
    def stopTest(self, test):
        super().stopTest(test)
        self.checkpoint()
class TestRunner(unittest.TextTestRunner):
    resultclass = Result
result = unittest.main(module=None, argv=['run_tests', '-v', module, '--durations=5'],
                       testRunner=TestRunner, exit=False).result
result.checkpoint(complete=True)
sys.exit(not result.wasSuccessful())
'''


class Runner:
    def __init__(self, mods, par, timeout, logs, hist, results=None):
        self.mods, self.par, self.timeout, self.logs, self.hist = mods, par, timeout, logs, hist
        self.lock = threading.Lock()
        self.state = {}         # module -> {start, current, t_test, n, bad, proc, killed, done}
        self.t0 = time.time()
        self.results_path = results or os.path.join(logs, "results.json")
        self.manifest_lock = threading.Lock()
        self.cancelled = threading.Event()
        self.results = {m: dict(module=m, status="queued", complete=False,
                                counts={k: 0 for k in COUNTS}, duration_seconds=None,
                                timeout_seconds=timeout, timed_out=False, returncode=None,
                                log=os.path.abspath(os.path.join(logs, m + ".log"))) for m in mods}

    def checkpoint_path(self, m):
        return os.path.join(self.logs, m + ".result.json")

    def manifest(self, status="running"):
        with self.manifest_lock:
            modules = list(self.results.values())
            complete = bool(modules) and all(m["complete"] for m in modules)
            data = dict(schema_version=1, status=status, complete=complete,
                        success=status == "finished" and complete and all(m["status"] == "passed" for m in modules),
                        duration_seconds=round(time.time() - self.t0, 3), modules=modules,
                        counts={k: sum(m["counts"][k] for m in modules) for k in COUNTS})
            path = os.path.abspath(self.results_path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            os.replace(path + ".tmp", path)
            return data

    def result(self, m, **fields):
        with self.manifest_lock:
            self.results[m].update(fields)
        self.manifest()

    def read_counts(self, m):
        try:
            with open(self.checkpoint_path(m), encoding="utf-8") as f:
                data = json.load(f)
            if any(type(data[k]) is not int or data[k] < 0 for k in COUNTS):
                return {}, False
            return {k: data[k] for k in COUNTS}, data.get("complete") is True
        except (OSError, ValueError, KeyError, TypeError):
            return {}, False

    def say(self, msg):
        with self.lock:
            print(f"[{time.time() - self.t0:6.0f}s] {msg}", flush=True)

    def expected(self, m):
        return self.hist.get(m, {}).get("secs", DEFAULT_S)

    def kill(self, st):
        """The module's process and everything it started (servers, pool workers: on Windows they outlive a
        killed parent)."""
        p = st.get("proc")
        if p is None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def timed_out(self, st):
        # The leader can exit while a descendant still holds its stdout pipe open. Kill the process group too.
        st["killed"] = True
        self.kill(st)

    def run(self, m):
        try:
            return self.run_module(m)
        except Exception as e:
            st = self.state.get(m, {})
            self.kill(st)
            if st.get("proc") is not None:
                st["proc"].wait()
                if st["proc"].stdout is not None:
                    st["proc"].stdout.close()
            dt = time.time() - st.get("start", self.t0)
            counts, _ = self.read_counts(m)
            self.result(m, status="error", complete=False, duration_seconds=round(dt, 3), error=str(e),
                        counts=counts or {k: 0 for k in COUNTS},
                        returncode=st["proc"].returncode if st.get("proc") is not None else None)
            self.say(f"== {m:26} ERROR  {e}")
            return m, "FAIL", dt

    def run_module(self, m):
        with self.lock:
            st = self.state[m] = {"start": time.time(), "current": None, "t_test": time.time(), "n": 0, "bad": [],
                                  "proc": None, "killed": False}
        with open(os.path.join(self.logs, f"{m}.log"), "w", encoding="utf-8") as log:
            # Synchronize launch with cancellation so an interrupt cannot miss a newly spawned child.
            with self.lock:
                if self.cancelled.is_set():
                    return m, "FAIL", 0.0
                p = st["proc"] = subprocess.Popen(
                    [sys.executable, "-B", "-u", "-c", CHILD, m, self.checkpoint_path(m),
                     str(max(1, int(self.timeout - 10)))], cwd=TESTS, env=dict(os.environ, PYTHONUNBUFFERED="1"),
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                    start_new_session=os.name != "nt")      # killpg reaches its children
            self.result(m, status="running")
            timer = threading.Timer(self.timeout, self.timed_out, (st,))
            timer.start()
            try:
                for line in p.stdout:
                    log.write(line)
                    log.flush()
                    self.parse(m, st, line.rstrip())
                p.wait()
            finally:
                timer.cancel()
                timer.join()
                p.stdout.close()
        dt = time.time() - st["start"]
        counts, complete = self.read_counts(m)
        status = ("interrupted" if self.cancelled.is_set() else "timed_out" if st["killed"] else
                  "incomplete" if not complete else
                  "passed" if p.returncode == 0 and not any(counts[k] for k in
                      ("failures", "errors", "unexpected_successes")) else "failed")
        self.result(m, status=status, complete=complete and status in ("passed", "failed"),
                    counts=counts or {k: 0 for k in COUNTS}, duration_seconds=round(dt, 3),
                    timed_out=st["killed"], returncode=p.returncode)
        res = "HANG" if st["killed"] else "OK" if status == "passed" else "FAIL"
        self.say(f"== {m:26} {res:6} {dt:6.1f}s  {st['n']} tests"
                 + (f"  bad: {', '.join(st['bad'])}" if st["bad"] else ""))
        return m, res, dt

    def parse(self, m, st, line):
        if line.startswith("Timeout ("):                        # faulthandler's dump before the timeout
            self.say(f"{m:29} slow    near its timeout: stacks in {os.path.join(self.logs, m + '.log')}")
        s = START.match(line)
        if s:
            st["current"] = s.group(2).split(".", 1)[-1]        # its clock runs since the previous verdict
        v = VERDICT.search(line)
        if v and st["current"]:
            verdict, dt = v.group(1), time.time() - st["t_test"]
            st["n"] += 1
            if verdict in ("FAIL", "ERROR"):
                st["bad"].append(st["current"])
            if verdict != "ok" or dt > SLOW_S:
                self.say(f"{m:29} {verdict[:7]:7} {dt:6.1f}s  {st['current']}")
            st["current"], st["t_test"] = None, time.time()

    def eta(self):
        """Seconds left: the running modules' expected rest and the queued ones' times, over --par slots."""
        now = time.time()
        with self.lock:
            rest = [max(5.0, self.expected(m) - (now - s["start"])) for m, s in self.state.items() if "done" not in s]
            queued = [self.expected(m) for m in self.mods if m not in self.state]
        slots = sorted(rest + [0.0] * max(0, self.par - len(rest)))
        for q in queued:                        # in start order, each into the slot that frees first
            slots[0] += q
            slots.sort()
        return max(slots) if slots else 0.0

    def progress(self, m, s):
        now = time.time()
        tests = self.hist.get(m, {}).get("tests")
        left = self.expected(m) - (now - s["start"])
        return (f"{m} {now - s['start']:.0f}s {s['n']}/{tests if tests else '?'}"
                + (f" ~{left:.0f}s left" if left > 0 else " (over its usual time)")
                + (f" in {s['current']} {now - s['t_test']:.0f}s" if s["current"] else ""))

    def ticker(self, stop):
        while not stop.wait(STATUS_S):
            with self.lock:
                running = [(m, s) for m, s in self.state.items() if "done" not in s]
                done = len(self.state) - len(running)
            if running:
                self.say(f"{done}/{len(self.mods)} modules done, ETA ~{self.eta() / 60:.1f} min | running: "
                         + "; ".join(self.progress(m, s) for m, s in running))

    def save(self, m, dt):
        self.hist[m] = {"secs": round(dt, 1), "tests": self.state[m]["n"]}
        os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
        with open(HISTORY, "w", encoding="utf-8") as f:
            json.dump(self.hist, f, indent=1, sort_keys=True)

    def all(self):
        os.makedirs(self.logs, exist_ok=True)
        self.manifest()
        for m in self.mods:
            try:
                os.unlink(self.checkpoint_path(m))     # never accept a prior run's successful result
            except FileNotFoundError:
                pass
        self.say(f"{len(self.mods)} modules, {self.par} at a time, timeout {self.timeout} s, "
                 f"ETA ~{self.eta() / 60:.1f} min, logs in {self.logs}")
        stop = threading.Event()
        threading.Thread(target=self.ticker, args=(stop,), daemon=True).start()
        res = []
        ex = cf.ThreadPoolExecutor(self.par)
        try:
            for f in cf.as_completed([ex.submit(self.run, m) for m in self.mods]):
                m, r, dt = f.result()
                self.state[m]["done"] = True
                res.append((m, r, dt))
                if r != "HANG":                         # a killed run's time says nothing
                    self.save(m, dt)
        except KeyboardInterrupt:
            self.say("interrupted: stopping the running modules")
            self.cancelled.set()
            with self.lock:
                for s in list(self.state.values()):
                    self.kill(s)
            ex.shutdown(wait=True, cancel_futures=True)
            for m, result in self.results.items():
                if result["status"] in ("queued", "running"):
                    self.result(m, status="not_run" if result["status"] == "queued" else "interrupted")
            self.manifest("interrupted")
            return 130
        except Exception:
            self.cancelled.set()
            with self.lock:
                for s in list(self.state.values()):
                    self.kill(s)
            ex.shutdown(wait=True, cancel_futures=True)
            self.manifest("error")
            raise
        finally:
            stop.set()
        ex.shutdown()
        bad = [(m, r) for m, r, _ in res if r != "OK"]
        self.say(f"done: {len(res) - len(bad)} OK, {len(bad)} not OK"
                 + (": " + ", ".join(f"{m} ({r})" for m, r in bad) if bad else ""))
        return 0 if self.manifest("finished")["success"] else 1


def main():
    ap = argparse.ArgumentParser(description="Run the server tests module by module, with live progress.")
    ap.add_argument("modules", nargs="*", help="test modules (default: every server/tests/test_*.py)")
    ap.add_argument("--par", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=1500)
    ap.add_argument("--logs", default=os.path.join(BUILD, "test-logs"))
    ap.add_argument("--results", help="JSON manifest path (default: <logs>/results.json)")
    a = ap.parse_args()
    mods = [m[:-3] if m.endswith(".py") else m for m in a.modules] or \
        sorted(os.path.basename(p)[:-3] for p in glob.glob(os.path.join(TESTS, "test_*.py")))
    os.makedirs(a.logs, exist_ok=True)
    try:
        with open(HISTORY, encoding="utf-8") as f:
            hist = json.load(f)
    except (OSError, ValueError):
        hist = {}
    mods = list(dict.fromkeys(mods))
    mods.sort(key=lambda m: -hist.get(m, {}).get("secs", DEFAULT_S))     # longest first: none ends the run alone
    # SIGTERM gets the same cleanup and durable incomplete result as Ctrl-C. SIGKILL leaves the initial/running
    # manifest unsuccessful; no signal handler can finalize that case.
    def interrupt(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        return Runner(mods, max(1, a.par), a.timeout, os.path.abspath(a.logs), hist, a.results).all()
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    sys.exit(main())
