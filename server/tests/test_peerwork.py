"""peerwork.py: compute sharing's wire format (no pickle), the job checks, the Built round trip, and a requester using
a helper (and building locally when the helper is gone or of another code)."""
import socket
import sys
import threading
import unittest
from concurrent.futures import Future
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import farfield  # noqa: E402
import peerwork  # noqa: E402


def built(seed=1):
    rng = np.random.default_rng(seed)
    return farfield.Built(pos=[1.0, 2.0, 3.0], positions=rng.random((9, 3)), normals=rng.random((9, 3)),
                          uvs=rng.random((9, 2)), planar=rng.random((9, 2)), tris=rng.integers(0, 9, (8, 3)),
                          grid=rng.random((3, 3)).astype(np.float32), err=0.5, sink=None, skirt=2.0, size=512.0)


def header(**over):
    h = {"op": "far_geometry", "lat": 39.28, "lon": -76.59, "tile": [1, -2, 3], "zoom": 13, "q": None, "ref_zoom": 14,
         "with_sink": True, "layout": {"cells": 64, "block": 1024, "mesh": True, "tex": None},
         "settings": {"roads": True, "grading": True}}
    h.update(over)
    return h


class WireTest(unittest.TestCase):
    def test_frames_round_trip(self):
        a, b = socket.socketpair()
        peerwork.send_msg(a, {"op": "x", "n": 3}, [b"abc", b""])
        h, blobs = peerwork.recv_msg(b)
        self.assertEqual((h, blobs), ({"op": "x", "n": 3}, [b"abc", b""]))
        a.sendall(b"EVIL" + b"\0" * 10)
        with self.assertRaises(ValueError):
            peerwork.recv_msg(b)

    def test_built_round_trip_without_pickle(self):
        b = built()
        d, blobs = peerwork.built_msg(b)
        self.assertTrue(all(blob.startswith(b"\x93NUMPY") for blob in blobs))
        c = peerwork.built_from(d, blobs)
        for n in peerwork.BUILT_ARRAYS:
            np.testing.assert_array_equal(getattr(b, n), getattr(c, n))
        self.assertEqual((c.pos, c.err, c.sink, c.skirt, c.size), (b.pos, b.err, b.sink, b.skirt, b.size))
        obj = np.array([{"a": 1}], dtype=object)       # an object array is refused, never unpickled
        with self.assertRaises(ValueError):
            peerwork._from_npy(peerwork._npy(obj))


class CheckTest(unittest.TestCase):
    def test_a_good_job(self):
        op, settings, kw = peerwork.check_job(header())
        self.assertEqual(op, "far_geometry")
        self.assertEqual(kw["tile"].key, "1_-2_3")
        self.assertEqual(settings, (True, True, False, False, False))

    def test_bad_jobs_refused(self):
        for bad in (header(op="os.system"), header(lat=91.0), header(lon=float("nan")), header(tile=[1, 2]),
                    header(zoom=40), header(q=-1.0), header(layout={"cells": 7, "block": 1024}),
                    header(lat="39"), header(op="far_tile", tex={"px": 256, "chain": ["http://evil"]})):
            with self.assertRaises(ValueError, msg=str(bad)[:80]):
                peerwork.check_job(bad)


class LocalPool:
    def __init__(self):
        self.calls = 0

    def submit(self, fn, *args, **kw):
        self.calls += 1
        f = Future()
        f.set_result(("local", args[3].key))
        return f


def far_geometry(*args):        # (the job's name is what the pool routes by)
    raise AssertionError("not called here")


class FakeHelper:
    """A helper on a local port answering every job with a fixed Built (or refusing another code)."""

    def __init__(self, code=None):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen()
        self.port = self.srv.getsockname()[1]
        self.code = code or peerwork.code_id()
        self.jobs = []
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while True:
            try:
                s, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self.conn, args=(s,), daemon=True).start()

    def conn(self, s):
        try:
            h, _ = peerwork.recv_msg(s)
            peerwork.send_msg(s, {"ok": h.get("code") == self.code, "workers": 2, "code": self.code})
            while True:
                h, _ = peerwork.recv_msg(s)
                self.jobs.append(h["tile"])
                d, blobs = peerwork.built_msg(built(len(self.jobs)))
                peerwork.send_msg(s, {"ok": True, "built": d}, blobs)
        except (ConnectionError, OSError, ValueError):
            pass


class PoolTest(unittest.TestCase):
    def args(self, i):
        return (1, 39.28, -76.59, farfield.Tile(1, i, 0), 13, None, 14, True, farfield.Layout(64))

    def test_helper_used_then_local(self):
        helper, local = FakeHelper(), LocalPool()
        pool = peerwork.SharedFarPool(local, [("127.0.0.1", helper.port)], {"roads": True}, None)
        for _ in range(100):
            if pool.free.qsize() == 2:
                break
            threading.Event().wait(0.02)
        self.assertEqual(pool.remote_slots(), 2, "earthd keeps this many more far tiles in flight")
        futs = [pool.submit(far_geometry, *self.args(i)) for i in range(4)]
        out = [f.result(10) for f in futs]
        remote = [o for o in out if isinstance(o, farfield.Built)]
        self.assertEqual(len(remote), 2, "the helper's two slots")
        self.assertEqual(local.calls, 2, "the rest built here")
        self.assertEqual(len(helper.jobs), 2)

    def test_other_code_not_used(self):
        helper, local = FakeHelper(code="another-0"), LocalPool()
        pool = peerwork.SharedFarPool(local, [("127.0.0.1", helper.port)], {}, None)
        threading.Event().wait(0.3)
        self.assertEqual((pool.free.qsize(), pool.remote_slots()), (0, 0))
        self.assertEqual(pool.submit(far_geometry, *self.args(0)).result(5), ("local", "1_0_0"))


if __name__ == "__main__":
    unittest.main()
