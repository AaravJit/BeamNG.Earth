"""Compute sharing (the user, 2026-10-22: "test out multiplayer and compute sharing"; the hybrid plan's step 6: "shared
generation among nearby players"): far tiles built on other machines.

A far tile is a pure function of the anchor (its latitude / longitude), the tile, the zooms, the anchor's height
quantum, the layout and the server's settings: any machine running the same server code builds the same tile (the
generation is deterministic; across CPUs numpy's float64 maths may differ in the last bit: ~1e-9 m, measured between
two machines, 2026-10-22), downloading what it needs itself. So a player's world server (earthd --peers HOST:PORT
..., or BNGE_PEERS) can send its far tiles to helpers - another player's PC, a home server, a Steam Deck's own PC -
while its own worker processes are busy, and gets the geometry (and, for far_tile, the texture's bytes) back.

  python3 server/peerwork.py serve [--port 47620] [--workers N] [--cache-dir DIR]   (a helper)

Wire format (TCP, both ways): b"BNGE", a u32 header length, the header (JSON), a u16 blob count, each blob a u64 length
and its bytes. Arrays travel as .npy (numpy's format, allow_pickle off): no pickle, no code, ever crosses the network.
A helper runs only the JOBS below, with every argument type- and range-checked (check_job). The first message on a
connection is "hello": both sides' PROTOCOL, the server's version and the fingerprint of its source (code_id) must
match, else the helper is not used (a different version would build different tiles).

The requester (SharedFarPool, in place of earthd's far-tile process pool) keeps one connection per helper worker,
sends a job to a free one, and builds the tile itself when no helper is free or one fails (an error, a lost
connection: that helper is left out for HELPER_RETRY_S).
"""
import argparse
import hashlib
import io
import json
import logging
import math
import os
import queue
import socket
import socketserver
import struct
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

import numpy as np

log = logging.getLogger("earthd.peerwork")

PROTOCOL = 1
PORT = 47620
MAGIC = b"BNGE"
MAX_HEADER = 1 << 16
MAX_BLOBS = 8
MAX_BLOB = 64 << 20
JOBS = ("far_geometry", "far_tile")
CONNECT_S = 3.0
JOB_TIMEOUT_S = 120.0
HELPER_RETRY_S = 60.0
MAX_SLOTS = 32                  # helper connections a requester keeps at most (its far threads: one per slot)
SETTINGS = ("roads", "grading", "road_widths", "airports", "sea")


# --- identity ----------------------------------------------------------------------------------------------------
_CODE_ID = None
_ROOT = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def source_id(server_dir=None):
    """A fingerprint of the server's Python sources (server/*.py): written into a packaged app as CODE_ID at its
    build (tools/build_app.py), whose modules are bytecode only."""
    here = server_dir or os.path.dirname(os.path.abspath(__file__))
    h = hashlib.sha256()
    for name in sorted(os.listdir(here)):
        if name.endswith(".py"):
            with open(os.path.join(here, name), "rb") as f:
                h.update(name.encode() + b"\0" + f.read())
    return h.hexdigest()[:16]


def code_id():
    """This server's code: its sources' fingerprint (CODE_ID in a packaged app) and the native module's version. Two
    servers with the same one build the same tiles."""
    global _CODE_ID
    if _CODE_ID is None:
        try:
            with open(os.path.join(_ROOT, "CODE_ID"), encoding="ascii") as f:
                src = f.read().strip()
        except OSError:
            src = source_id()
        try:
            import fast
            native = getattr(fast._ef, "VERSION", 0)
        except Exception:
            native = 0
        _CODE_ID = f"{src}-{native}"
    return _CODE_ID


def version():
    try:
        with open(os.path.join(_ROOT, "VERSION")) as f:
            return f.read().strip()
    except OSError:
        return "?"


# --- framing -----------------------------------------------------------------------------------------------------
def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), 1 << 20))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return bytes(buf)


def send_msg(sock, header, blobs=()):
    h = json.dumps(header, separators=(",", ":")).encode()
    parts = [MAGIC, struct.pack("<I", len(h)), h, struct.pack("<H", len(blobs))]
    for b in blobs:
        parts += [struct.pack("<Q", len(b)), b]
    sock.sendall(b"".join(parts))


def recv_msg(sock):
    if _recv_exact(sock, 4) != MAGIC:
        raise ValueError("not a BeamNG.Earth peer")
    n = struct.unpack("<I", _recv_exact(sock, 4))[0]
    if n > MAX_HEADER:
        raise ValueError("header too large")
    header = json.loads(_recv_exact(sock, n).decode())
    if not isinstance(header, dict):
        raise ValueError("bad header")
    k = struct.unpack("<H", _recv_exact(sock, 2))[0]
    if k > MAX_BLOBS:
        raise ValueError("too many blobs")
    blobs = []
    for _ in range(k):
        m = struct.unpack("<Q", _recv_exact(sock, 8))[0]
        if m > MAX_BLOB:
            raise ValueError("blob too large")
        blobs.append(_recv_exact(sock, m))
    return header, blobs


def _npy(a):
    b = io.BytesIO()
    np.lib.format.write_array(b, np.ascontiguousarray(a), allow_pickle=False)
    return b.getvalue()


def _from_npy(b):
    return np.lib.format.read_array(io.BytesIO(b), allow_pickle=False)


BUILT_ARRAYS = ("positions", "normals", "uvs", "planar", "tris", "grid")


def built_msg(built):
    """(header dict, blobs) of a farfield.Built."""
    d = {"pos": [float(v) for v in built.pos], "err": float(built.err),
         "sink": None if built.sink is None else float(built.sink), "skirt": float(built.skirt),
         "size": float(built.size), "arrays": [n for n in BUILT_ARRAYS if getattr(built, n) is not None]}
    return d, [_npy(getattr(built, n)) for n in d["arrays"]]


def built_from(d, blobs):
    import farfield
    arrays = dict(zip(d["arrays"], (_from_npy(b) for b in blobs)))
    return farfield.Built(pos=list(d["pos"]), positions=arrays.get("positions"), normals=arrays.get("normals"),
                          uvs=arrays.get("uvs"), planar=arrays.get("planar"), tris=arrays.get("tris"),
                          grid=arrays.get("grid"), err=float(d["err"]), sink=d["sink"], skirt=float(d["skirt"]),
                          size=float(d["size"]))


# --- jobs --------------------------------------------------------------------------------------------------------
def _num(v, lo, hi, what, allow_none=False):
    if v is None and allow_none:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise ValueError(f"bad {what}: {v!r}")
    return v


def _int(v, lo, hi, what):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"bad {what}: {v!r}")
    return v


def job_header(fn_name, args, settings):
    """The request header for blockworker.<fn_name>(*args) (the far pool's submit arguments)."""
    aid, lat, lon, tile, zoom, q, ref_zoom, with_sink, layout = args[:9]
    h = {"op": fn_name, "lat": float(lat), "lon": float(lon), "tile": [tile.level, tile.i, tile.j],
         "zoom": int(zoom), "q": None if q is None else float(q), "ref_zoom": None if ref_zoom is None else int(ref_zoom),
         "with_sink": bool(with_sink),
         "layout": {"cells": layout.cells, "block": layout.block, "mesh": bool(layout.mesh),
                    "tex": list(layout.tex) if layout.tex is not None else None},
         "settings": dict(settings)}
    if fn_name == "far_tile":
        tex = args[9]
        h["tex"] = {"px": int(tex["px"]), "chain": list(tex["chain"]), "tone": float(tex["tone"]),
                    "leaf_on": bool(tex["leaf_on"]), "season_mode": str(tex["season_mode"]),
                    "mask_px": int(tex["mask"][1]) if tex.get("mask") and tex["mask"][1] else 0}
    return h


def check_job(h):
    """The validated job of a request header: (op, settings tuple, kwargs for run_job). ValueError otherwise."""
    import farfield
    import imagery
    op = h.get("op")
    if op not in JOBS:
        raise ValueError(f"unknown job {op!r}")
    lat = _num(h.get("lat"), -90.0, 90.0, "lat")
    lon = _num(h.get("lon"), -180.0, 180.0, "lon")
    t = h.get("tile")
    if not isinstance(t, list) or len(t) != 3:
        raise ValueError("bad tile")
    tile = farfield.Tile(_int(t[0], -2, 16, "level"), _int(t[1], -100000, 100000, "i"), _int(t[2], -100000, 100000, "j"))
    zoom = _int(h.get("zoom"), 0, 16, "zoom")
    q = _num(h.get("q"), 1e-6, 1.0, "q", allow_none=True)
    ref_zoom = h.get("ref_zoom")
    ref_zoom = None if ref_zoom is None else _int(ref_zoom, 0, 16, "ref_zoom")
    lay = h.get("layout") or {}
    tex_px = lay.get("tex")
    if tex_px is not None and (not isinstance(tex_px, list) or len(tex_px) > 32
                               or not all(isinstance(v, int) and 16 <= v <= 4096 for v in tex_px)):
        raise ValueError("bad layout tex")
    layout = farfield.Layout(_int(lay.get("cells"), 1, 1024, "cells"), _int(lay.get("block"), 1, 1 << 16, "block"),
                             bool(lay.get("mesh")), tuple(tex_px) if tex_px is not None else None)
    s = h.get("settings") or {}
    settings = tuple(bool(s.get(k)) for k in SETTINGS)
    kw = dict(lat=lat, lon=lon, tile=tile, zoom=zoom, q=q, ref_zoom=ref_zoom, with_sink=bool(h.get("with_sink")),
              layout=layout)
    if op == "far_tile":
        tx = h.get("tex") or {}
        chain = tx.get("chain")
        if not isinstance(chain, list) or not chain or not all(isinstance(c, str) and c in imagery.SOURCES
                                                                for c in chain):
            raise ValueError("bad imagery chain")
        kw["tex"] = {"px": _int(tx.get("px"), 16, 4096, "px"), "chain": chain,
                     "tone": _num(tx.get("tone"), 0.0, 4.0, "tone"), "leaf_on": bool(tx.get("leaf_on")),
                     "season_mode": str(tx.get("season_mode"))[:16],
                     "mask_px": _int(tx.get("mask_px", 0), 0, 4096, "mask px")}
    return op, settings, kw


def run_job(op, kw, out_dir):
    """In a helper's worker process: the job's (header, blobs) reply."""
    import blockworker
    aid = _anchor_id(kw["lat"], kw["lon"])
    args = (aid, kw["lat"], kw["lon"], kw["tile"], kw["zoom"], kw["q"], kw["ref_zoom"], kw["with_sink"], kw["layout"])
    if op == "far_geometry":
        built = blockworker.far_geometry(*args)
        d, blobs = built_msg(built)
        return {"ok": True, "built": d}, blobs
    tx = kw["tex"]
    stem = os.path.join(out_dir, f"peer_{os.getpid()}_{threading.get_ident()}")
    tex = {"path": stem + ".dds", "px": tx["px"], "mask": (stem + "_mask.dds", tx["mask_px"]) if tx["mask_px"] else None,
           "chain": tx["chain"], "tone": tx["tone"], "leaf_on": tx["leaf_on"], "season_mode": tx["season_mode"],
           "online": True}
    built, got = blockworker.far_tile(*args, tex)
    d, blobs = built_msg(built)
    head = {"ok": True, "built": d, "texture": None}
    if got is not None:
        with open(tex["path"], "rb") as f:
            blobs.append(f.read())
        head["texture"] = {"used": got[1], "blob": len(blobs) - 1}
        if tex["mask"] and os.path.exists(tex["mask"][0]):
            with open(tex["mask"][0], "rb") as f:
                blobs.append(f.read())
            head["mask_blob"] = len(blobs) - 1
    for p in (tex["path"], tex["mask"][0] if tex["mask"] else None):
        if p:
            try:
                os.remove(p)
            except OSError:
                pass
    return head, blobs


_AIDS = {}


def _anchor_id(lat, lon):
    """A helper process's anchor id for a requester's anchor (blockworker keys its graders by id)."""
    k = (round(lat, 9), round(lon, 9))
    if k not in _AIDS:
        _AIDS[k] = 1000000 + len(_AIDS)
    return _AIDS[k]


# --- the helper ----------------------------------------------------------------------------------------------------
class Helper:
    """A helper's worker pools, one per settings combination (made on first use), and its TCP service."""

    def __init__(self, cache_dir, workers, out_dir):
        self.cache_dir, self.workers, self.out_dir = cache_dir, workers, out_dir
        self.pools = {}
        self.lock = threading.Lock()
        self.done = 0

    def pool(self, settings):
        import multiprocessing
        import blockworker
        import pools
        with self.lock:
            p = self.pools.get(settings)
            if p is None:
                ctx = multiprocessing.get_context("spawn")
                roads, grading, widths, airports, sea = settings
                p = self.pools[settings] = pools.RespawningPool(
                    "peer far tile", self.workers, mp_context=ctx, initializer=blockworker.init,
                    initargs=(self.cache_dir, False, roads, grading and roads, widths and roads, False,
                              ctx.Queue(maxsize=1), logging.getLogger().level, airports, sea))
                log.info("helper: worker pool for settings %s (%d processes)", dict(zip(SETTINGS, settings)),
                         self.workers)
            return p

    def hello(self):
        return {"ok": True, "protocol": PROTOCOL, "version": version(), "code": code_id(), "workers": self.workers}

    def handle(self, sock, addr):
        try:
            h, _ = recv_msg(sock)
            mine = self.hello()
            if h.get("op") != "hello" or h.get("protocol") != PROTOCOL or h.get("code") != mine["code"]:
                send_msg(sock, dict(mine, ok=False, error="a different BeamNG.Earth version"))
                return
            send_msg(sock, mine)
            while True:
                h, _ = recv_msg(sock)
                t0 = time.perf_counter()
                try:
                    op, settings, kw = check_job(h)
                    head, blobs = self.pool(settings).submit(run_job, op, kw, self.out_dir).result(JOB_TIMEOUT_S)
                except Exception as e:      # a bad request or a failed build: told, the connection kept
                    send_msg(sock, {"ok": False, "error": f"{type(e).__name__}: {e}"[:500]})
                    continue
                send_msg(sock, head, blobs)
                with self.lock:
                    self.done += 1
                    n = self.done
                if n % 50 == 1:
                    log.info("helper: %d jobs done (last: %s %s from %s, %.2f s)", n, op, kw["tile"].key, addr[0],
                             time.perf_counter() - t0)
        except (ConnectionError, OSError, ValueError) as e:
            log.debug("helper: %s: %s", addr, e)
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def serve(self, host, port):
        helper = self

        class H(socketserver.BaseRequestHandler):
            def handle(self):
                helper.handle(self.request, self.client_address)

        class S(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True
        with S((host, port), H) as srv:
            log.info("helper: serving far tiles on %s:%d (%d workers, code %s, version %s)", host, port,
                     self.workers, code_id(), version())
            srv.serve_forever()


# --- the requester -------------------------------------------------------------------------------------------------
class _Conn:
    def __init__(self, host, port):
        self.host, self.port = host, port
        self.sock = None

    def open(self):
        s = socket.create_connection((self.host, self.port), timeout=CONNECT_S)
        s.settimeout(JOB_TIMEOUT_S)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        send_msg(s, {"op": "hello", "protocol": PROTOCOL, "version": version(), "code": code_id()})
        h, _ = recv_msg(s)
        if not h.get("ok"):
            s.close()
            raise ConnectionError(f"{self.host}:{self.port}: {h.get('error', 'refused')} "
                                  f"(theirs {h.get('version')} {h.get('code')}, ours {version()} {code_id()})")
        self.sock = s
        return int(h.get("workers") or 1)

    def call(self, header):
        if self.sock is None:
            self.open()
        try:
            send_msg(self.sock, header)
            return recv_msg(self.sock)
        except Exception:
            self.close()
            raise

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


class SharedFarPool:
    """earthd's far-tile pool with helpers: submit(fn, *args) as the process pool's; blockworker.far_geometry and
    far_tile go to a free helper connection when there is one, else (or when it fails) to the local pool."""

    def __init__(self, local, peers, settings, out_dir):
        self.local, self.settings, self.out_dir = local, dict(settings), out_dir
        self.free = queue.Queue()
        self.down = {}                      # (host, port) -> monotonic time it failed
        self.stats = {"remote": 0, "local": 0, "failed": 0}
        self.lock = threading.Lock()
        self.slots = 0                      # helper connections joined and not left out after a failure
        self.io = ThreadPoolExecutor(MAX_SLOTS, thread_name_prefix="peer-io")
        for host, port in peers:
            threading.Thread(target=self._join, args=(host, port), daemon=True, name="peer-join").start()

    def _join(self, host, port):
        c = _Conn(host, port)
        try:
            n = c.open()
        except Exception as e:
            log.warning("compute sharing: helper %s:%d not used: %s", host, port, e)
            with self.lock:
                self.down[(host, port)] = time.monotonic()
            return
        with self.lock:
            n = min(n, MAX_SLOTS - self.slots)
            self.slots += max(n, 0)
        if n <= 0:
            c.close()
            return
        log.info("compute sharing: helper %s:%d joined (%d workers)", host, port, n)
        self.free.put(c)
        for _ in range(n - 1):
            self.free.put(_Conn(host, port))

    def remote_slots(self):
        """Far tiles the helpers can build at once (earthd keeps its own workers' count plus these in flight)."""
        return self.slots

    def _back(self, conn):
        with self.lock:
            self.slots += 1
        self.free.put(conn)

    def submit(self, fn, *args, **kwargs):
        name = getattr(fn, "__name__", "")
        if name in JOBS and not kwargs:
            try:
                conn = self.free.get_nowait()
            except queue.Empty:
                conn = None
            if conn is not None:
                return self.io.submit(self._remote, conn, fn, name, args)
        with self.lock:
            self.stats["local"] += 1
        return self.local.submit(fn, *args, **kwargs)

    def _remote(self, conn, fn, name, args):
        try:
            head, blobs = conn.call(job_header(name, args, self.settings))
            if not head.get("ok"):
                raise RuntimeError(head.get("error", "failed"))
            built = built_from(head["built"], blobs)
            out = built
            if name == "far_tile":
                tex, got = args[9], None
                t = head.get("texture")
                if t is not None:
                    data = blobs[t["blob"]]
                    _write(tex["path"], data)
                    if tex.get("mask") and head.get("mask_blob") is not None:
                        _write(tex["mask"][0], blobs[head["mask_blob"]])
                    got = (len(data), t["used"])
                out = (built, got)
            with self.lock:
                self.stats["remote"] += 1
            self.free.put(conn)
            return out
        except Exception as e:
            with self.lock:
                self.stats["failed"] += 1
                self.slots -= 1
                n = self.stats["failed"]
            if n <= 3 or n % 50 == 0:
                log.warning("compute sharing: %s at %s:%d failed (%s); built here", name, conn.host, conn.port, e)
            conn.close()
            threading.Timer(HELPER_RETRY_S, self._back, args=(conn,)).start()
            return self.local.submit(fn, *args).result()

    def shutdown(self, *a, **kw):
        self.io.shutdown(wait=False)
        return self.local.shutdown(*a, **kw)


def _write(path, data):
    import staging
    tmp = staging.tmp_for(path)
    with open(tmp, "wb") as f:
        f.write(data)
    staging.replace(tmp, path)


def parse_peers(text):
    """[(host, port)] of "host[:port] host2[:port] ..." (commas or spaces)."""
    out = []
    for item in (text or "").replace(",", " ").split():
        host, _, port = item.rpartition(":") if ":" in item else (item, "", str(PORT))
        out.append((host or item, int(port)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="BeamNG.Earth compute sharing: build far tiles for other players")
    ap.add_argument("cmd", choices=["serve"])
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--cache-dir", default=os.path.expanduser("~/.cache/beamng-earth"))
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    out = os.path.join(a.cache_dir, "peer-out")
    os.makedirs(out, exist_ok=True)
    Helper(a.cache_dir, a.workers, out).serve(a.host, a.port)


if __name__ == "__main__":
    sys.exit(main())
