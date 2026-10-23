"""Render mapped Overture guard rails as a lightweight per-tile mesh.

Only guard rails already present in the map data are rendered. The mesh uses the
game's shared `guardrail` material and is render-only; it does not add collision
objects to every road tile.
"""
import json
import logging
import math

import numpy as np

import mapcells
import mapgeom

log = logging.getLogger("earthd.guardrails")

POST_SPACING_M = 2.0
POST_WIDTH_M = 0.08
POST_HEIGHT_M = 0.72
RAIL_DEPTH_M = 0.07
RAIL_BOTTOM_M = 0.62
RAIL_TOP_M = 0.78
BBOX_PAD_M = 2.0


def _is_guardrail(feats, i):
    """Use Overture's normalized class, falling back to the preserved OSM tags."""
    classes = feats.attrs.get("class")
    subtypes = feats.attrs.get("subtype")
    cls = str(classes[i]).strip().lower() if classes is not None else ""
    subtype = str(subtypes[i]).strip().lower() if subtypes is not None else ""
    if subtype == "barrier" and cls == "guard_rail":
        return True
    tags = feats.attrs.get("tags")
    if tags is None:
        return False
    try:
        values = json.loads(str(tags[i]))
    except (TypeError, ValueError):
        return False
    return str(values.get("barrier", "")).strip().lower() == "guard_rail"


def _at(line, arc, distance):
    """Point and unit tangent at arc length distance along a 2D polyline."""
    k = min(max(int(np.searchsorted(arc, distance, side="right") - 1), 0), len(line) - 2)
    span = max(float(arc[k + 1] - arc[k]), 1e-9)
    t = float(np.clip((distance - arc[k]) / span, 0.0, 1.0))
    d = line[k + 1] - line[k]
    length = max(float(np.hypot(d[0], d[1])), 1e-9)
    return line[k] + t * d, d / length


def _clip_segment(a, b, box):
    """Clip segment a-b to (x0, y0, x1, y1); return clipped endpoints or None."""
    x0, y0, x1, y1 = box
    dx, dy = float(b[0] - a[0]), float(b[1] - a[1])
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, float(a[0] - x0)), (dx, float(x1 - a[0])),
                 (-dy, float(a[1] - y0)), (dy, float(y1 - a[1]))):
        if abs(p) < 1e-12:
            if q < 0.0:
                return None
            continue
        r = q / p
        if p < 0.0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
        if t0 > t1:
            return None
    return a + t0 * (b - a), a + t1 * (b - a)


def _surface_z(frame, points, elev, decks):
    """Height at XY positions; snap barriers on bridges to the generated deck, else use terrain."""
    points = np.asarray(points, np.float64).reshape(-1, 2)
    z = np.full(len(points), np.nan, np.float64)
    best = np.full(len(points), np.inf, np.float64)
    for deck in decks or ():
        line = np.asarray(deck.get("n", ()), np.float64)
        if len(line) < 2:
            continue
        a, b = line[:-1, :2], line[1:, :2]
        d = b - a
        l2 = np.maximum(np.sum(d * d, axis=1), 1e-12)
        v = points[:, None, :] - a[None, :, :]
        t = np.clip(np.sum(v * d[None, :, :], axis=2) / l2[None, :], 0.0, 1.0)
        near = a[None, :, :] + t[:, :, None] * d[None, :, :]
        dist = np.hypot(points[:, None, 0] - near[:, :, 0], points[:, None, 1] - near[:, :, 1])
        k = np.argmin(dist, axis=1)
        row = np.arange(len(points))
        dmin = dist[row, k]
        use = (dmin <= float(deck.get("w", 0.0)) / 2.0 + 2.0) & (dmin < best)
        if use.any():
            seg_z = line[k, 2] + t[row, k] * (line[k + 1, 2] - line[k, 2])
            z[use], best[use] = seg_z[use], dmin[use]
    missing = ~np.isfinite(z)
    if missing.any():
        la, lo, _ = frame.enu_to_geodetic(points[missing, 0], points[missing, 1],
                                          np.zeros(int(missing.sum()), np.float64))
        ground = np.asarray(elev(la, lo), np.float64)
        z[missing] = frame.geodetic_to_enu(la, lo, ground)[2]
    return z


def _quad(triangles, a, b, c, d):
    triangles.extend(((a, b, c), (a, c, d)))


def _prism(vertices, triangles, uvs, p0, p1, z0, z1, depth, bottom, top, origin):
    """A rectangular beam following p0-p1, with offsets above the sampled ground."""
    tangent = np.asarray(p1, np.float64) - np.asarray(p0, np.float64)
    length = float(np.hypot(tangent[0], tangent[1]))
    if length < 1e-3:
        return
    tangent /= length
    normal = np.array([-tangent[1], tangent[0]]) * (depth / 2.0)
    a, b = np.asarray(p0, np.float64), np.asarray(p1, np.float64)
    base = len(vertices)
    corners = (a + normal, a - normal, b - normal, b + normal)
    for q, zz, v in ((corners[0], z0 + bottom, 0), (corners[1], z0 + bottom, 1),
                     (corners[2], z1 + bottom, 1), (corners[3], z1 + bottom, 0),
                     (corners[0], z0 + top, 0), (corners[1], z0 + top, 1),
                     (corners[2], z1 + top, 1), (corners[3], z1 + top, 0)):
        vertices.append((float(q[0] - origin[0]), float(q[1] - origin[1]), float(zz - origin[2])))
        uvs.append((float(length * v), float(0 if len(vertices) - base <= 4 else 1)))
    # Outward-facing bottom, top, sides and ends.
    _quad(triangles, base + 0, base + 3, base + 2, base + 1)
    _quad(triangles, base + 4, base + 5, base + 6, base + 7)
    _quad(triangles, base + 0, base + 4, base + 7, base + 3)
    _quad(triangles, base + 1, base + 2, base + 6, base + 5)
    _quad(triangles, base + 0, base + 1, base + 5, base + 4)
    _quad(triangles, base + 3, base + 7, base + 6, base + 2)


def _post(vertices, triangles, uvs, centre, tangent, z, origin):
    """A square post, oriented with the mapped line."""
    t = np.asarray(tangent, np.float64)
    t /= max(float(np.hypot(t[0], t[1])), 1e-9)
    n = np.array([-t[1], t[0]])
    c = np.asarray(centre, np.float64)
    half = POST_WIDTH_M / 2.0
    # Counter-clockwise footprint, then its top.
    footprint = (c - t * half - n * half, c + t * half - n * half,
                 c + t * half + n * half, c - t * half + n * half)
    base = len(vertices)
    for q in footprint:
        vertices.append((float(q[0] - origin[0]), float(q[1] - origin[1]), float(z - 0.04 - origin[2])))
        uvs.append((0.0, 0.0))
    for q in footprint:
        vertices.append((float(q[0] - origin[0]), float(q[1] - origin[1]),
                         float(z + POST_HEIGHT_M - origin[2])))
        uvs.append((1.0, 1.0))
    _quad(triangles, base + 0, base + 3, base + 2, base + 1)
    _quad(triangles, base + 4, base + 5, base + 6, base + 7)
    _quad(triangles, base + 0, base + 1, base + 5, base + 4)
    _quad(triangles, base + 1, base + 2, base + 6, base + 5)
    _quad(triangles, base + 2, base + 3, base + 7, base + 6)
    _quad(triangles, base + 3, base + 0, base + 4, base + 7)


def mesh(frame, infra_fn, exact, elev, origin=(0.0, 0.0, 0.0), decks=()):
    """Build a tile-local (positions, triangles, UVs) mesh from mapped barrier=guard_rail lines.

    Every 2 m a post is placed. Rails are clipped to the exact tile bounds so adjacent tile
    meshes meet without duplicate sections. The shape is render-only in the Lua client.
    """
    if infra_fn is None:
        return None
    x0, y0, x1, y1 = map(float, exact)
    corners_x = np.array([x0, x0, x1, x1], np.float64)
    corners_y = np.array([y0, y1, y0, y1], np.float64)
    la, lo, _ = frame.enu_to_geodetic(corners_x, corners_y, np.zeros(4, np.float64))
    try:
        keys = mapcells.cells_for_points(la, lo, pad_lat=0.00005, pad_lon=0.00005, max_cells=64)
    except (ValueError, OverflowError):
        return None

    seen = set()
    rail_sections, post_sites, query_points = [], [], []
    for key in keys:
        try:
            feats = infra_fn(key)
        except Exception as ex:
            log.debug("guardrails: infrastructure cell %s unavailable: %s", key, ex)
            continue
        if feats is None or not len(feats):
            continue
        classes = feats.attrs.get("class")
        if classes is None and feats.attrs.get("tags") is None:
            continue
        boxes = feats.boxes(frame)
        for i in range(len(feats)):
            fid = str(feats.ids[i])
            if fid in seen or int(feats.kind[i]) != mapgeom.LINE or not _is_guardrail(feats, i):
                continue
            b = boxes[i]
            if b[2] < x0 - BBOX_PAD_M or b[0] > x1 + BBOX_PAD_M or b[3] < y0 - BBOX_PAD_M or b[1] > y1 + BBOX_PAD_M:
                continue
            seen.add(fid)
            for part in feats.parts(i):
                if not part:
                    continue
                lonlat = np.asarray(part[0], np.float64)
                if len(lonlat) < 2:
                    continue
                e, n = mapgeom.enu_xy(feats, frame, lonlat)
                line = np.column_stack((e, n))
                seglen = np.hypot(*np.diff(line, axis=0).T)
                line = line[np.r_[True, seglen > 1e-4]]
                if len(line) < 2:
                    continue
                seglen = np.hypot(*np.diff(line, axis=0).T)
                arc = np.r_[0.0, np.cumsum(seglen)]
                total = float(arc[-1])
                if total < 0.05:
                    continue
                sample = np.unique(np.r_[arc, np.arange(0.0, total, POST_SPACING_M), total])
                for sa, sb in zip(sample[:-1], sample[1:]):
                    if sb - sa < 1e-4:
                        continue
                    a, ta = _at(line, arc, float(sa))
                    b, _ = _at(line, arc, float(sb))
                    clipped = _clip_segment(a, b, (x0, y0, x1, y1))
                    if clipped is None:
                        continue
                    p0, p1 = clipped
                    if float(np.hypot(*(p1 - p0))) < 0.05:
                        continue
                    k = len(query_points)
                    query_points.extend((p0, p1))
                    rail_sections.append((k, k + 1, p0, p1))
                for s in np.arange(0.0, total + 1e-6, POST_SPACING_M):
                    p, tangent = _at(line, arc, float(s))
                    if x0 <= p[0] < x1 and y0 <= p[1] < y1:
                        k = len(query_points)
                        query_points.append(p)
                        post_sites.append((k, p, tangent))

    if not query_points:
        return None
    z = _surface_z(frame, np.asarray(query_points, np.float64), elev, decks)
    vertices, triangles, uvs = [], [], []
    for i0, i1, p0, p1 in rail_sections:
        _prism(vertices, triangles, uvs, p0, p1, float(z[i0]), float(z[i1]),
               RAIL_DEPTH_M, RAIL_BOTTOM_M, RAIL_TOP_M, origin)
    for i, p, tangent in post_sites:
        _post(vertices, triangles, uvs, p, tangent, float(z[i]), origin)
    if not triangles:
        return None
    return (np.asarray(vertices, np.float32), np.asarray(triangles, np.int32), np.asarray(uvs, np.float32))
