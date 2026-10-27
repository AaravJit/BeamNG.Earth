"""The US Federal Railroad Administration's Highway-Rail Crossing Inventory (Form 71, current; public domain, a work of
the US government; updated daily on data.transportation.gov, dataset m2f8-22s6): each public and private crossing's
place and warning devices, so a level crossing gets the gates, lamps or crossbucks it really has (railways.
crossing_props; the user, 2026-10-27, OK'd "1 and 2": OSM rail tags and the US rail data).

One SODA query per map cell (its at-grade crossings that are not closed), kept on disk as JSON for CACHE_DAYS; outside
the US box no request is made. Unreachable: None, and the crossings take the road class's kit.
"""
import json
import logging
import os
import threading
import time
import urllib.parse

import numpy as np

log = logging.getLogger("earthd.fra")

URL = "https://data.transportation.gov/resource/m2f8-22s6.json"
FIELDS = ("latitude", "longitude", "countroadwaygatearms", "countflashinglightpair", "countmastmountedflashinglight",
          "countcantileveroverlane", "numbercrossbuckassemblies")
CACHE_DAYS = 30
MATCH_M = 40.0          # m between a map crossing and the inventory's point (its places are approximate)
# the US and its territories, roughly (no request elsewhere: the inventory has nothing there)
US_BOXES = ((24.0, -125.5, 49.6, -66.5), (51.0, -180.0, 72.0, -129.0), (18.5, -161.0, 22.5, -154.5),
            (17.6, -68.0, 18.6, -65.2))


def in_us(lat, lon):
    return any(a <= lat <= c and b <= lon <= d for a, b, c, d in US_BOXES)


def kit_of(rec):
    """"gate" / "flash" / "cross" of an inventory record."""
    n = lambda k: int(float(rec.get(k) or 0))
    if n("countroadwaygatearms") > 0:
        return "gate"
    if n("countflashinglightpair") > 0 or n("countmastmountedflashinglight") > 0 or n("countcantileveroverlane") > 0:
        return "flash"
    return "cross"


class Crossings:
    """The inventory's crossings by map cell, fetched once and kept (thread-safe)."""

    def __init__(self, cache_dir, fetcher):
        self.dir = os.path.join(cache_dir, "fra") if cache_dir else None
        self.fetcher = fetcher
        self.mem = {}
        self.lock = threading.Lock()
        self.pool = None
        self.pending = {}                   # cell -> Future of a prefetch in flight

    def prefetch(self, keys):
        """Start reading cells keys in the background (a road tile's at its start: the request runs alongside the
        tile's other fetches instead of after them)."""
        from concurrent.futures import ThreadPoolExecutor
        with self.lock:
            if self.pool is None:
                self.pool = ThreadPoolExecutor(2, thread_name_prefix="fra")
            for k in keys:
                if str(k) not in self.mem and str(k) not in self.pending:
                    self.pending[str(k)] = self.pool.submit(self._cell, k)

    def cell(self, key):
        """[(lat, lon, kit)] of map cell key's at-grade crossings, or None (not the US, or unreachable)."""
        with self.lock:
            f = self.pending.get(str(key))
        if f is not None:
            try:
                return f.result()
            except Exception:
                return None
        return self._cell(key)

    def _cell(self, key):
        """[(lat, lon, kit)] of map cell key's at-grade crossings, or None (not the US, or unreachable)."""
        lon0, lat0, lon1, lat1 = (float(v) for v in key.bounds())
        if not in_us(0.5 * (lat0 + lat1), 0.5 * (lon0 + lon1)):
            return None
        k = str(key)
        with self.lock:
            if k in self.mem:
                return self.mem[k]
        path = os.path.join(self.dir, f"{k.replace('/', '_')}.json") if self.dir else None
        got = None
        if path and os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_DAYS * 86400:
            try:
                with open(path, encoding="utf-8") as fh:
                    got = [tuple(r) for r in json.load(fh)]
            except (OSError, ValueError):
                got = None
        if got is None and self.fetcher is not None:
            where = (f"within_box(geocoded_lat_long, {lat1}, {lon0}, {lat0}, {lon1}) "
                     "AND crossingposition = 'At Grade' AND reasondescription != 'Closed'")
            q = URL + "?" + urllib.parse.urlencode({"$where": where, "$select": ",".join(FIELDS), "$limit": 5000})
            try:
                rows = json.loads(self.fetcher.get(q, kind="demand").body.decode("utf-8"))
                got = [(float(r["latitude"]), float(r["longitude"]), kit_of(r)) for r in rows
                       if r.get("latitude") and r.get("longitude")]
                if path:
                    os.makedirs(self.dir, exist_ok=True)
                    tmp = path + ".part"
                    with open(tmp, "w", encoding="utf-8") as fh:
                        json.dump(got, fh)
                    os.replace(tmp, path)
            except Exception as e:      # unreachable / refused: the road classes' kits for now
                log.debug("fra: %s: %s: %s", k, type(e).__name__, e)
                with self.lock:
                    self.pending.pop(k, None)
                return None
        with self.lock:
            self.mem[k] = got
            self.pending.pop(k, None)
        return got

    def kits(self, frame, keys):
        """fn(xy (2,), road class) -> the nearest inventory crossing's kit within MATCH_M, or None, over cells keys."""
        pts = [r for k in keys for r in (self.cell(k) or ())]
        if not pts:
            return None
        a = np.array([[r[0], r[1]] for r in pts], np.float64)
        e, n, _ = frame.geodetic_to_enu(a[:, 0], a[:, 1], np.zeros(len(a)))
        xy = np.stack([np.asarray(e), np.asarray(n)], -1)
        kit = [r[2] for r in pts]

        def look(p, _cls):
            d = np.hypot(*(xy - np.asarray(p)[:2]).T)
            j = int(np.argmin(d))
            return kit[j] if d[j] <= MATCH_M else None
        return look
