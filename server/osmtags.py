"""OpenStreetMap road tags (lane count, width, sidewalks, speed limit, lighting, smoothness...) for a map cell's
roads, joined to Overture's road segments by their OpenStreetMap way id (the user, 2026-10-22: "map data sources that
have more info about the roads, lane count, road types").

Overture's transportation theme has no lane count (removed in 2024, not back in schema v2.0.0) and few widths, but
98.6 % of its segments come from OpenStreetMap and keep the way id (sources[1].record_id "w<id>@<version>"). OSM US's
Layercake publishes every OSM highway as one GeoParquet file (https://layercake.openstreetmap.us/layers/highways/,
ODbL, ~29 GB, 2,467 row groups, spatially clustered, rebuilt about weekly), with the tags as columns and a bbox struct.
Like overture.py, only what a cell needs is read, through rangeread (netfetch's limits, the block cache), with DuckDB
reading the blocks: the footer once (9 MB), the bbox columns of the row groups whose statistics touch the cell, then
the tag columns of the row groups that hold a matching way (no geometry). The tag values are parsed in the query
(lanes "2;3" -> 2, width "7 m" / "23'" -> metres, maxspeed "30 mph" -> km/h).

The file is rewritten in place: a version is pinned by its ETag (the cache directory; every range request carries
If-Match, rangeread.Changed when it moved on), and the next version is taken on the next start (version()).

A layer of its own is not needed: mapdata joins the tags into the roads layer when it builds a cell (all processes
read the same cell file, so a road's lanes and width agree everywhere). Unreachable: the cell keeps no tags and the
roads use their width guesses (roads.carriageway), as before.
"""
import logging
import os
import re
import threading
import time

import numpy as np

import netfetch
import overture
import rangeread

log = logging.getLogger("earthd.mapdata")

URL = "https://data.openstreetmap.us/layercake/highways.parquet"
HOST = "data.openstreetmap.us"
VERSION_TTL_S = 7 * 86400       # a pinned version is checked again after this long (the file changes ~weekly)
RETRY_S = 3600                  # the source unreachable: asked again after this long (offline, blocked, gone)
PATH = "osm/highways.parquet"   # (the path DuckDB reads it under)

# The query: OSM way id and the parsed tags (NULL where absent or not understood).
_NUM = r"'^\s*([0-9]+(?:\.[0-9]+)?)'"
COLUMNS = {
    "lanes": f"TRY_CAST(regexp_extract(lanes, '^\\s*([0-9]+)', 1) AS INTEGER)",
    "width": (f"TRY_CAST(regexp_extract(width, {_NUM}, 1) AS DOUBLE) * "
              "CASE WHEN width LIKE '%ft%' OR width LIKE '%''%' THEN 0.3048 ELSE 1.0 END"),
    "maxspeed": (f"TRY_CAST(regexp_extract(maxspeed, {_NUM}, 1) AS DOUBLE) * "
                 "CASE WHEN maxspeed LIKE '%mph%' THEN 1.609344 ELSE 1.0 END"),
    "sidewalk": "sidewalk",
    "lit": "lit",
    "smoothness": "smoothness",
    "osm_surface": "surface",
    "osm_oneway": "oneway",
    "osm_layer": "TRY_CAST(layer AS INTEGER)",
}
USED = {"id", "bbox", "lanes", "width", "maxspeed", "sidewalk", "lit", "smoothness", "surface", "oneway", "layer"}
FLOATS = ("lanes", "width", "maxspeed", "osm_layer")
TEXTS = ("sidewalk", "lit", "smoothness", "osm_surface", "osm_oneway")


def way_id(record):
    """The OSM way id of an Overture source record ("w1360453999@2" -> 1360453999), else None."""
    m = re.match(r"^w(\d+)", record or "")
    return int(m.group(1)) if m else None


class OsmTags:
    """Tags by OSM way id per map cell, from the pinned Layercake version."""

    def __init__(self, raw_dir, fetcher, url=URL):
        self.raw_dir, self.fetcher, self.url = raw_dir, fetcher, url
        self._lock = threading.Lock()
        self._remote = None         # (etag, RemoteFile)
        self._checked = 0.0
        self._reader = overture.Reader(raw_dir, fetcher)

    def version(self, kind="demand"):
        """(etag, RemoteFile) of the version in use: the one pinned on disk while fresh, else the server's."""
        with self._lock:
            if self._remote is not None and time.time() - self._checked < VERSION_TTL_S:
                return self._remote
            pin = os.path.join(self.raw_dir, "osmtags", "pinned")
            etag = size = None
            try:
                with open(pin) as f:
                    etag, size, when = f.read().split("\n")[:3]
                    size, when = int(size), float(when)
                if time.time() - when > VERSION_TTL_S:
                    etag = None
            except (OSError, ValueError):
                etag = None
            if etag is None:
                down = os.path.join(self.raw_dir, "osmtags", "unreachable")
                try:
                    recent = time.time() - os.path.getmtime(down) < RETRY_S
                except OSError:
                    recent = False
                if recent:
                    raise rangeread.RangeError(f"{self.url}: unreachable at the last try")
                try:
                    r = self.fetcher.get(self.url, kind=kind, headers={"Range": "bytes=0-15"})
                    etag = r.headers.get("etag", "").strip()
                    m = rangeread.CONTENT_RANGE.match(r.headers.get("content-range", ""))
                    if not etag or not m or m.group(3) == "*":
                        raise rangeread.RangeError(f"{self.url}: no ETag or size")
                except Exception:
                    os.makedirs(os.path.dirname(down), exist_ok=True)
                    open(down, "w").close()
                    raise
                size = int(m.group(3))
                os.makedirs(os.path.dirname(pin), exist_ok=True)
                with open(pin + ".tmp", "w") as f:
                    f.write(f"{etag}\n{size}\n{time.time()}\n")
                os.replace(pin + ".tmp", pin)
            d = os.path.join(self.raw_dir, "osmtags", re.sub(r"[^A-Za-z0-9]+", "_", etag).strip("_"))
            self._remote = (etag, rangeread.RemoteFile([self.url], size, d, self.fetcher, if_match=etag))
            self._checked = time.time()
            return self._remote

    def _forget(self):
        with self._lock:
            self._remote = None
            try:
                os.remove(os.path.join(self.raw_dir, "osmtags", "pinned"))
            except OSError:
                pass

    def query(self, bounds, kind="demand"):
        """{way id: {column: value}} of the OSM highways whose bbox touches bounds (lon0, lat0, lon1, lat1). A version
        that changed under the reads is dropped and the query asked once more on the new one."""
        for attempt in (0, 1):
            etag, remote = self.version(kind)
            try:
                return self._query(remote, bounds, kind)
            except rangeread.Changed:
                if attempt:
                    raise
                log.info("osmtags: %s changed (was %s), taking the new version", self.url, etag)
                self._forget()
        return {}

    def _query(self, remote, bounds, kind):
        lon0, lat0, lon1, lat1 = bounds
        rd = self._reader
        files = {PATH: remote}
        con, proto, errors = rd._connect(files, kind)
        try:
            uri = f"'{proto}://{PATH}'"
            tail = remote.read(max(0, remote.size - overture.FOOTER_GUESS), remote.size, kind)
            n = int.from_bytes(tail[-8:-4], "little")
            if n + 8 > len(tail):
                remote.read(remote.size - n - 8, remote.size, kind)
            meta = con.execute(f"""SELECT row_group_id, row_group_num_rows, path_in_schema, stats_min, stats_max,
                dictionary_page_offset, data_page_offset, total_compressed_size FROM parquet_metadata({uri})
                ORDER BY row_group_id, column_id""").fetchall()
            groups, first = {}, 0
            for rg, nrows, col, smin, smax, dict_off, data_off, size in meta:
                g = groups.get(rg)
                if g is None:
                    g = groups[rg] = {"first": first, "n": nrows, "bbox": {}, "cols": {}}
                    first += nrows
                offs = [o for o in (dict_off, data_off) if o]
                if not offs:
                    continue
                top, leaf = col.split(", ")[0], col.split(", ")[-1]
                g["cols"].setdefault(top, []).append((min(offs), min(offs) + size))
                if top == "bbox":
                    g["bbox"][leaf] = (smin, smax)

            def stat(g, leaf, i, default):
                try:
                    return float(g["bbox"][leaf][i])
                except (KeyError, TypeError, ValueError):
                    return default
            cand = [rg for rg, g in sorted(groups.items())
                    if stat(g, "xmin", 0, -1e9) <= lon1 and stat(g, "xmax", 1, 1e9) >= lon0
                    and stat(g, "ymin", 0, -1e9) <= lat1 and stat(g, "ymax", 1, 1e9) >= lat0]
            if not cand:
                return {}
            want = {c for rg in cand for c in groups[rg]["cols"] if c in USED}
            wanted = {PATH: [r for rg in cand for c, rs in groups[rg]["cols"].items() if c in want for r in rs]}
            rd._fetch(files, wanted, kind)
            only = " OR ".join(f"file_row_number BETWEEN {groups[rg]['first']} AND "
                               f"{groups[rg]['first'] + groups[rg]['n'] - 1}" for rg in cand)
            cols = ", ".join(f"{expr} AS \"{name}\"" for name, expr in COLUMNS.items())
            rows = con.execute(f"""SELECT id, {cols} FROM read_parquet({uri}, file_row_number = true)
                WHERE ({only}) AND bbox.xmin <= {lon1!r} AND bbox.xmax >= {lon0!r}
                AND bbox.ymin <= {lat1!r} AND bbox.ymax >= {lat0!r}""").fetchall()
        except Exception:
            for e in errors:
                if isinstance(e, rangeread.Changed):
                    raise e
            raise
        finally:
            con.close()
        names = list(COLUMNS)
        return {int(r[0]): {k: v for k, v in zip(names, r[1:]) if v is not None} for r in rows}


def join(feats, tags):
    """The tags of each road feature (its source record's way id) as attrs columns: floats (NaN where unknown) for
    FLOATS, text ('' where unknown) for TEXTS. feats: an Overture roads Features (attrs src_record)."""
    import mapgeom as mg
    recs = feats.attrs.get("src_record")
    n = len(feats)
    ids = [way_id(str(recs[i])) if recs is not None else None for i in range(n)]
    for c in FLOATS:
        feats.attrs["osm_" + c if not c.startswith("osm_") else c] = np.array(
            [float(tags.get(w, {}).get(c, np.nan)) if w is not None else np.nan for w in ids], np.float64)
    for c in TEXTS:
        feats.attrs[c if c.startswith("osm_") else "osm_" + c] = mg.text_column(
            [str(tags.get(w, {}).get(c, "")) if w is not None else "" for w in ids])
    return feats
