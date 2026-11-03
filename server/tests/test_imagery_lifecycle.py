"""Imagery fetches racing shutdown: real executors, synthetic loads, no network."""
import faulthandler
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import imagery  # noqa: E402


def close_during_submission(kind):
    """Run in a child: a regression must time out instead of hanging the test runner at exit."""
    faulthandler.dump_traceback_later(5)
    running, release = threading.Event(), threading.Event()
    producer_locked, close_started = threading.Event(), threading.Event()
    cancelled_callback = threading.Event()

    class BlockedImagery(imagery.Imagery):
        def _load(self, key):
            running.set()
            release.wait(10)
            return None

    with tempfile.TemporaryDirectory() as root:
        im = BlockedImagery(root, fetch_workers=1)
        # Make both lanes single-worker so one future runs and the others stay queued.
        if kind == "urgent":
            im.urgent_pool.shutdown()
            im.urgent_pool = ThreadPoolExecutor(1)
        finish = im._finish

        def mark_cancel(key, future):
            if future.cancelled():
                cancelled_callback.set()
            finish(key, future)

        im._finish = mark_cancel
        keys = [(imagery.SOURCES["eox2016"], 2, i, 0) for i in range(3)]
        with im._lock:
            active = im._future(keys[0], kind)
            pending = im._future(keys[1], kind)
        assert running.wait(2), "the synthetic load never started"
        submitted, errors = [], []

        def submit():
            try:
                with im._lock:
                    producer_locked.set()
                    assert close_started.wait(2)
                    # Before the fix, cancellation enters _finish while close owns the executor's
                    # shutdown lock. With the consistent lock order, close waits for this producer.
                    cancelled_callback.wait(0.5)
                    submitted.append(im._future(keys[2], kind))
            except BaseException as exc:
                errors.append(exc)

        def close():
            try:
                assert producer_locked.wait(2)
                close_started.set()
                im.close()
            except BaseException as exc:
                errors.append(exc)

        producer = threading.Thread(target=submit, name="imagery-producer", daemon=True)
        closer = threading.Thread(target=close, name="imagery-closer", daemon=True)
        producer.start()
        closer.start()
        producer.join(3)
        closer.join(3)
        release.set()
        assert not producer.is_alive(), "submit deadlocked against close"
        assert not closer.is_alive(), "close deadlocked in a cancellation callback"
        assert not errors, errors
        assert len(submitted) == 1
        assert pending.cancelled(), "queued fetch was not cancelled"
        assert submitted[0].cancelled(), "fetch queued just before close was not cancelled"
        assert active.result(timeout=2) is None
        im.pool.shutdown(wait=True)
        im.urgent_pool.shutdown(wait=True)
        # Repeated close must be harmless, and neither lane may accept new work after close.
        im.close()
        for pool in (im.pool, im.urgent_pool):
            try:
                pool.submit(lambda: None)
            except RuntimeError:
                pass
            else:
                raise AssertionError("fetch executor still accepts work after close")
    faulthandler.cancel_dump_traceback_later()


class ShutdownTest(unittest.TestCase):
    def run_race(self, kind):
        command = (
            f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parent)!r}); "
            f"from test_imagery_lifecycle import close_during_submission; close_during_submission({kind!r})"
        )
        try:
            child = subprocess.run([sys.executable, "-c", command], capture_output=True, text=True,
                                   timeout=9)
        except subprocess.TimeoutExpired as exc:
            self.fail(f"{kind} fetch/close deadlock:\n{exc.stderr}")
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)

    def test_close_during_demand_submission(self):
        self.run_race("demand")

    def test_close_during_urgent_submission(self):
        self.run_race("urgent")


if __name__ == "__main__":
    unittest.main()
