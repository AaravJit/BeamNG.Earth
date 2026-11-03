"""pools.py: a process pool whose worker died is made again (GitHub #76, #91)."""
import os
import multiprocessing
import signal
import subprocess
import sys
import unittest
import unittest.mock
from concurrent.futures.process import BrokenProcessPool

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import pools  # noqa: E402


def new_pool():
    # Match earthd/peerwork: recycling leaves executor-management threads alive.
    # Linux's default fork can copy a wakeup lock they hold into the new worker,
    # which then deadlocks in concurrent.futures._python_exit during shutdown.
    return pools.RespawningPool("test", 1, mp_context=multiprocessing.get_context("spawn"))


def recycle_with_live_manager():
    """Force the inherited-lock hazard that made the old fork-based fixture hang."""
    other = new_pool()
    try:
        assert other.submit(abs, -1).result(timeout=10) == 1
        # This is the executor wakeup lock shown in the stuck worker's traceback.
        # Hold it while starting/recycling another pool; spawned workers must not
        # inherit it. Keep this deliberately hostile scenario in a bounded child.
        with other._pool._executor_manager_thread_wakeup._lock:
            p = new_pool()
            try:
                assert p.submit(abs, -2).result(timeout=10) == 2
                assert p.recycle("live executor manager")
                assert p.submit(abs, -3).result(timeout=10) == 3
            finally:
                p.shutdown()
    finally:
        other.shutdown()


class RespawnTest(unittest.TestCase):
    def test_a_dead_worker_is_replaced(self):
        p = new_pool()
        try:
            self.assertEqual(p.submit(abs, -3).result(), 3)
            with self.assertRaises(BrokenProcessPool):
                p.submit(os._exit, 1).result()      # the worker dies
            with self.assertLogs("earthd.pools", "WARNING"):
                self.assertEqual(p.submit(abs, -4).result(), 4)
            self.assertEqual(p.respawns, 1)
        finally:
            p.shutdown()

    def test_not_made_again_and_again(self):
        p = new_pool()
        try:
            with unittest.mock.patch.object(pools, "RESPAWN_S", 3600.0):
                for _ in range(2):
                    try:
                        p.submit(os._exit, 1).result()
                    except BrokenProcessPool:
                        pass
                with self.assertRaises(BrokenProcessPool):
                    p.submit(abs, -1)
            self.assertEqual(p.respawns, 1)
        finally:
            p.shutdown()

    def test_a_hung_worker_is_restarted(self):
        """A worker that never answers (alive, so the pool is not broken) is killed by recycle(): the call in flight
        ends with BrokenProcessPool and the next one is served (a road tile hung for minutes, 1.7.0-beta)."""
        import time
        p = new_pool()
        try:
            hung = p.submit(time.sleep, 3600)
            time.sleep(0.5)                     # (it is running)
            with self.assertLogs("earthd.pools", "WARNING"):
                self.assertTrue(p.recycle("test"))
            with self.assertRaises(BrokenProcessPool):
                hung.result(timeout=30)
            self.assertEqual(p.submit(abs, -5).result(timeout=30), 5)
            self.assertFalse(p.recycle("again at once"))    # (at most every RESPAWN_S)
        finally:
            p.shutdown()

    def test_jobs_in_flight_are_counted(self):
        """inflight (Server.live_busy: the saved areas' downloads wait for it): up at a submit, down when the job ends,
        fails or is cancelled, also when a pool made again cancels its futures (its own lock: no deadlock)."""
        import time
        p = new_pool()
        try:
            self.assertEqual(p.inflight, 0)
            f = p.submit(time.sleep, 0.3)
            self.assertEqual(p.inflight, 1)
            f.result(timeout=30)
            time.sleep(0.05)
            self.assertEqual(p.inflight, 0)
            with self.assertRaises(ZeroDivisionError):
                p.submit(divmod, 1, 0).result(timeout=30)
            time.sleep(0.05)
            self.assertEqual(p.inflight, 0)
            hung = p.submit(time.sleep, 3600)
            queued = p.submit(abs, -1)
            time.sleep(0.3)
            with self.assertLogs("earthd.pools", "WARNING"):
                self.assertTrue(p.recycle("test"))
            for fut in (hung, queued):
                try:
                    fut.result(timeout=30)
                except Exception:
                    pass
            time.sleep(0.05)
            self.assertEqual(p.inflight, 0)
        finally:
            p.shutdown()

    def test_recycle_and_shutdown_with_another_executor_lock_held(self):
        """The production start method avoids copied executor locks, including at child exit."""
        code = (f"import sys; sys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r}); "
                "from test_pools import recycle_with_live_manager; recycle_with_live_manager()")
        proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, start_new_session=os.name != "nt")
        try:
            output, _ = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            # A shutdown deadlock must fail promptly and leave no worker processes.
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            else:
                os.killpg(proc.pid, signal.SIGKILL)
            output, _ = proc.communicate(timeout=10)
            self.fail("recycled pool did not shut down within 30 seconds:\n" + output)
        self.assertEqual(proc.returncode, 0, output)


if __name__ == "__main__":
    unittest.main()
