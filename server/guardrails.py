"""Render Overture's mapped road guardrails as repeated Forest items.

The infrastructure layer is already part of MapData. This module only turns mapped
LineStrings tagged/classified as guard rails into stable, tile-owned item rows.
"""
import json
import math

import numpy as np

import mapcells
import mapgeom

SPACING_M = 3.0
MODEL_HEIGHT_M = 1.0
BBOX_PAD_M = 3.0


def _is_guardrail(feats, i):
    """Overture's normalized class, with the original OSM tag as a fallback."""
    classes = feats.attrs.get("class")
    subtype = feats.attrs.get("subtype")
    cls = str(classes[i]).strip().lower() if classes is not None else ""
    sub = str(subtype[i]).strip().lower() if subtype is not None else ""
    if sub == "barrier" and cls == "guard_rail":
        return True
    if sub != "barrier" or cls not in ("", "barrier"):
        return False
    tags = feats.attrs.get("tags")
    if tags is None:
        return False
    try:
        value = json.loads(str(tags[i]))
    except (TypeError, ValueError):
        return False
    return str(value.get("barrier", "")).strip().lower() == "guard_rail"


def _project_z(points, line):
    """Distance to and height on a 3D polyline for each 2D point."""
    line = np.asarray(line, np.float64)
    if len(line) < 2:
        return np.full(len(points), np.inf), np.full(len(points), np.nan)
    a, b = line[:-1, :2], line[1:, :2]
    d = b - a
    l2 = np.maximum(np.sum(d * d, axis=1), 1e-12)
    v = points[:, None, :] - a[None, :, :]
    t = np.clip(np.sum(v * d[None, :, :], axis=2) / l2[None, :], 0.0, 1.0)
    p = a[None, :, :] + t[:, :, None] * d[None, :, :]
    dist = np.hypot(points[:, None, 0] - p[:, :, 0], points[:, None, 1] - p[:, :, 1])
    k = np.argmin(dist, axis=1)
    j = np.arange(len(points))
    z = line[k, 2] + t[j, k] * (line[k + 1, 2] - line[k, 2])
    return dist[j, k], z


def _heights(frame, points, elev, decks):
    """Ground heights, using a nearby generated bridge deck instead of the terrain below it."""
    z = np.full(len(points), np.nan, np.float64)
    best = np.full(len(points), np.inf, np.float64)
    for deck in decks or ():
        line = deck.get("n")
        if line is None or len(line) < 2:
            continue
        dist, dz = _project_z(points, line)
        reach = float(deck.get("w", 0.0)) / 2.0 + 5.0
        use = (dist <= reach) & (dist < best)
        z[use], best[use] = dz[use], dist[use]
    missing = ~np.isfinite(z)
    if missing.any():
        la, lo, _ = frame.enu_to_geodetic(points[missing, 0], points[missing, 1],
                                          np.zeros(int(missing.sum()), np.float64))
        ground = np.asarray(elev(la, lo), np.float64)
        z[missing] = frame.geodetic_to_enu(la, lo, ground)[2]
    return z


def rows(frame, infra_fn, exact, elev, decks=()):
    """Return trees.lua rows for mapped guardrails intersecting a road tile.

    Each row is [group, x, y, z, yaw, scale, height, rank]. Sampling begins at half a
    model length along the complete source line, so neighbouring tiles agree at seams.
    """
    if infra_fn is None:
        return []
    x0, y0, x1, y1 = map(float, exact)
    lat, lon, _ = frame.enu_to_geodetic(
        np.array([x0, x0, x1, x1], np.float64),
        np.array([y0, y1, y0, y1], np.float64),
        np.zeros(4, np.float64),
    )
    try:
        if float(lon.max() - lon.min()) > 180.0:
            raise ValueError("tile crosses the antimeridian")
        keys = mapcells.cells_for_bbox(float(lat.min()), float(lon.min()),
                                       float(lat.max()), float(lon.max()), max_cells=64)
    except ValueError:
        keys = mapcells.cells_for_points(lat, lon, pad_lat=0.005, pad_lon=0.005)

    seen, stations = set(), []
    for key in keys:
        feats = infra_fn(key)
        if feats is None or not len(feats):
            continue
        boxes = feats.boxes(frame)
        for i in range(len(feats)):
            fid = str(feats.ids[i])
            if fid in seen:
                continue
            if int(feats.kind[i]) != mapgeom.LINE or not _is_guardrail(feats, i):
                continue
            b = boxes[i]
            if b[2] < x0 - BBOX_PAD_M or b[0] > x1 + BBOX_PAD_M or b[3] < y0 - BBOX_PAD_M or b[1] > y1 + BBOX_PAD_M:
                continue
            seen.add(fid)
            for part in feats.parts(i):
                if not part:
                    continue
                ring = np.asarray(part[0], np.float64)
                if len(ring) < 2:
                    continue
                e, n = mapgeom.enu_xy(feats, frame, ring)
                xy = np.column_stack((e, n))
                delta = np.diff(xy, axis=0)
                lengths = np.hypot(delta[:, 0], delta[:, 1])
                keep = np.r_[True, lengths > 1e-6]
                xy = xy[keep]
                if len(xy) < 2:
                    continue
                delta = np.diff(xy, axis=0)
                lengths = np.hypot(delta[:, 0], delta[:, 1])
                arc = np.r_[0.0, np.cumsum(lengths)]
                total = float(arc[-1])
                if total < SPACING_M:
                    continue
                at = np.arange(SPACING_M / 2.0, total - SPACING_M / 2.0 + 1e-6, SPACING_M)
                if not len(at):
                    continue
                j = np.clip(np.searchsorted(arc, at, side="right") - 1, 0, len(xy) - 2)
                frac = (at - arc[j]) / np.maximum(arc[j + 1] - arc[j], 1e-9)
                q = xy[j] + delta[j] * frac[:, None]
                # Half-open ownership makes every sample belong to exactly one tile.
                inside = ((q[:, 0] >= x0) & (q[:, 0] < x1) &
                          (q[:, 1] >= y0) & (q[:, 1] < y1))
                for p, d in zip(q[inside], delta[j[inside]]):
                    yaw = math.degrees(math.atan2(float(d[1]), float(d[0]))) % 360.0
                    stations.append((float(p[0]), float(p[1]), yaw))
    if not stations:
        return []
    xy = np.asarray([(p[0], p[1]) for p in stations], np.float64)
    z = _heights(frame, xy, elev, decks)
    return [["guardrail", round(float(p[0]), 2), round(float(p[1]), 2), round(float(zz), 2),
             round(float(station[2]), 1), 1.0, MODEL_HEIGHT_M, 0.0]
            for p, zz, station in zip(xy, z, stations)]
