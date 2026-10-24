"""Render mapped Overture guard rails as a lightweight per-tile mesh.

Only guard rails already present in the map data are rendered. The mesh uses the
game's shared `guardrail` material and is render-only; it does not add collision
objects to every road tile.
"""
import hashlib
import json
import logging
import math

import numpy as np

import mapcells
import mapgeom

log = logging.getLogger("earthd.guardrails")

POST_SPACING_M = 2.0
POST_WIDTH_M = 0.08
POST_HEIGHT_M = 0.88
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
    # source_tags is optional in Overture and may be absent, JSON null, or another
    # JSON value. Only an object can contain OSM tag keys.
    if not isinstance(values, dict):
        return False
    return str(values.get("barrier", "")).strip().lower() == "guard_rail"


def _deck_level_hint(feats, i):
    """Return True for a mapped bridge-level barrier, False for lower/ground level, else None.

    These tags belong to the barrier way itself. A negative layer must not be overwritten by a
    nearby bridge deck merely because both features overlap in XY.
    """
    tags = feats.attrs.get("tags")
    if tags is None:
        return None
    try:
        values = json.loads(str(tags[i]))
    except (TypeError, ValueError):
        return None
    if not isinstance(values, dict):
        return None

    norm = {str(k).strip().lower(): str(v).strip().lower() for k, v in values.items()}
    yes = {"yes", "true", "1"}
    no = {"no", "false", "0"}
    if norm.get("tunnel", "") in yes:
        return False

    layer = None
    try:
        layer = float(norm.get("layer", ""))
    except (TypeError, ValueError):
        pass
    if layer is not None and layer < 0:
        return False

    bridge = norm.get("bridge", "")
    if bridge in yes or bridge in {"viaduct", "movable", "swing", "drawbridge", "covered"}:
        return True
    if norm.get("location", "") in {"bridge", "on_bridge"}:
        return True
    if layer is not None:
        return layer > 0
    if bridge in no or norm.get("location", "") in {"underground", "underwater", "underground_passage"}:
        return False
    return None


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


DECK_AUTO_SNAP_TOLERANCE_M = 1.5  # An untagged barrier may be snapped to a deck only when terrain agrees.


def _surface_z(frame, points, elev, decks, deck_preference=None):
    """Sample terrain first; snap to a deck only when tags identify its level or heights agree.

    OSM barrier lines are 2-D. XY-only snapping is wrong at an overpass: a lower road's guardrail
    can otherwise be lifted onto the bridge above it. Explicit bridge/layer tags disambiguate levels;
    for untagged ways the terrain must be within a small tolerance of the candidate deck.
    """
    points = np.asarray(points, np.float64).reshape(-1, 2)
    if not len(points):
        return np.zeros(0, np.float64)

    la, lo, _ = frame.enu_to_geodetic(points[:, 0], points[:, 1], np.zeros(len(points), np.float64))
    ground = np.asarray(elev(la, lo), np.float64)
    ground_z = np.asarray(frame.geodetic_to_enu(la, lo, ground)[2], np.float64)

    deck_z = np.full(len(points), np.nan, np.float64)
    best = np.full(len(points), np.inf, np.float64)
    for deck in decks or ():
        line = np.asarray(deck.get("n", ()), np.float64)
        if len(line) < 2 or line.shape[1] < 3:
            continue
        a, b = line[:-1, :2], line[1:, :2]
        d = b - a
        l2 = np.maximum(np.sum(d * d, axis=1), 1e-12)
        v = points[:, None, :] - a[None, :, :]
        t = np.clip(np.sum(v * d[None, :, :], axis=2) / l2[None, :], 0.0, 1.0)
        near = a[None, :, :] + t[:, :, None] * d[None, :, :]
        dist = np.hypot(points[:, None, 0] - near[:, :, 0],
                        points[:, None, 1] - near[:, :, 1])
        k = np.argmin(dist, axis=1)
        row = np.arange(len(points))
        dmin = dist[row, k]
        use = (dmin <= float(deck.get("w", 0.0)) / 2.0 + 2.0) & (dmin < best)
        if use.any():
            seg_z = line[k, 2] + t[row, k] * (line[k + 1, 2] - line[k, 2])
            deck_z[use], best[use] = seg_z[use], dmin[use]

    if deck_preference is None:
        preference = np.full(len(points), -1, np.int8)  # unknown: infer conservatively from terrain
    else:
        preference = np.asarray([
            -1 if value is None else (1 if bool(value) else 0)
            for value in deck_preference
        ], np.int8)
        if len(preference) != len(points):
            raise ValueError("deck_preference must contain one value per sample point")

    found = np.isfinite(deck_z)
    explicit_bridge = found & (preference > 0)
    terrain_matches = found & (preference < 0) & (
        np.abs(deck_z - ground_z) <= DECK_AUTO_SNAP_TOLERANCE_M)
    use_deck = explicit_bridge | terrain_matches
    return np.where(use_deck, deck_z, ground_z)



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
    for q, zz, u in ((corners[0], z0 + bottom, 0), (corners[1], z0 + bottom, 0),
                     (corners[2], z1 + bottom, length), (corners[3], z1 + bottom, length),
                     (corners[0], z0 + top, 0), (corners[1], z0 + top, 0),
                     (corners[2], z1 + top, length), (corners[3], z1 + top, length)):
        vertices.append((float(q[0] - origin[0]), float(q[1] - origin[1]), float(zz - origin[2])))
        uvs.append((float(u), float(0 if len(vertices) - base <= 4 else 1)))
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
    rail_sections, post_sites, query_points, query_deck_hints = [], [], [], []
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
            deck_hint = _deck_level_hint(feats, i)
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
                    query_deck_hints.extend((deck_hint, deck_hint))
                    rail_sections.append((k, k + 1, p0, p1, deck_hint))
                for s in np.arange(0.0, total + 1e-6, POST_SPACING_M):
                    p, tangent = _at(line, arc, float(s))
                    if x0 <= p[0] < x1 and y0 <= p[1] < y1:
                        k = len(query_points)
                        query_points.append(p)
                        query_deck_hints.append(deck_hint)
                        post_sites.append((k, p, tangent, deck_hint))

    if not query_points:
        return None
    z = _surface_z(frame, np.asarray(query_points, np.float64), elev, decks,
                   deck_preference=query_deck_hints)
    vertices, triangles, uvs = [], [], []
    for i0, i1, p0, p1, _deck_hint_value in rail_sections:
        _prism(vertices, triangles, uvs, p0, p1, float(z[i0]), float(z[i1]),
               RAIL_DEPTH_M, RAIL_BOTTOM_M, RAIL_TOP_M, origin)
    for i, p, tangent, _deck_hint_value in post_sites:
        _post(vertices, triangles, uvs, p, tangent, float(z[i]), origin)
    if not triangles:
        return None
    return (np.asarray(vertices, np.float32), np.asarray(triangles, np.int32), np.asarray(uvs, np.float32))


# Stock Italy guardrail modules are 3 m long and overlap by 0.2 m at 2.8 m spacing.
# They are Forest items: the engine batches repeated shapes and caches their collision mesh.
MODULE_LENGTH_M = 3.0
MODULE_STEP_M = 2.8
MODULE_HEIGHT_M = 0.9
# The standard and reverse-facing models sit beside one another, rather than on the same line.
# This is the distance between their centre lines (0.25 m to either side of the mapped way).
TWO_SIDED_SPACING_M = 0.5


def _is_two_sided(feats, i):
    """OSM's two_sided=yes means the guardrail has a traffic-facing rail on both sides."""
    tags = feats.attrs.get("tags")
    if tags is None:
        return False
    try:
        values = json.loads(str(tags[i]))
    except (TypeError, ValueError):
        return False
    if not isinstance(values, dict):
        return False
    return str(values.get("two_sided", "")).strip().lower() in ("yes", "true", "1")


# Keep this tight: a central divider between adjacent bridge decks can be close to both inner parapets.
PARAPET_DUPLICATE_M = 0.7
PARAPET_LEVEL_TOLERANCE_M = 1.0
PARAPET_PARALLEL_DOT = 0.75
PARAPET_MATCH_SHARE = 0.75
PARAPET_MATCH_SAMPLES = 7


def _parapet_segments(parapets):
    """Flatten generated bridge-parapet centre lines into XY segments for matching."""
    starts, ends, z_starts, z_ends = [], [], [], []
    for parapet in parapets or ():
        line = np.asarray(parapet.get("line", ()) if isinstance(parapet, dict) else parapet,
                          np.float64).reshape(-1, 2)
        if len(line) < 2:
            continue
        z = np.asarray(parapet.get("z", ()) if isinstance(parapet, dict) else (), np.float64).reshape(-1)
        if len(z) != len(line):
            z = np.full(len(line), np.nan, np.float64)
        a, b = line[:-1], line[1:]
        d = b - a
        length = np.hypot(d[:, 0], d[:, 1])
        keep = length > 1e-6
        if keep.any():
            starts.append(a[keep])
            ends.append(b[keep])
            z_starts.append(z[:-1][keep])
            z_ends.append(z[1:][keep])
    if not starts:
        return None
    a, b = np.concatenate(starts), np.concatenate(ends)
    d = b - a
    length = np.hypot(d[:, 0], d[:, 1])
    return {
        "a": a,
        "b": b,
        "unit": d / length[:, None],
        "lo": np.minimum(a, b),
        "hi": np.maximum(a, b),
        "za": np.concatenate(z_starts),
        "zb": np.concatenate(z_ends),
    }


def _overlaps_bridge_parapet(a, b, segments, level=None):
    """Whether a module runs along a generated concrete parapet, not merely across it."""
    if segments is None:
        return False
    a, b = np.asarray(a, np.float64), np.asarray(b, np.float64)
    d = b - a
    length = float(np.hypot(d[0], d[1]))
    if length < 1e-6:
        return False
    tangent = d / length
    lo = np.minimum(a, b) - PARAPET_DUPLICATE_M
    hi = np.maximum(a, b) + PARAPET_DUPLICATE_M
    box = ((segments["hi"][:, 0] >= lo[0]) & (segments["lo"][:, 0] <= hi[0]) &
           (segments["hi"][:, 1] >= lo[1]) & (segments["lo"][:, 1] <= hi[1]))
    if not box.any():
        return False
    pa, pb, unit = segments["a"][box], segments["b"][box], segments["unit"][box]
    v = pb - pa
    v2 = np.maximum(np.sum(v * v, axis=1), 1e-12)
    samples = a[None, :] + np.linspace(0.1, 0.9, PARAPET_MATCH_SAMPLES)[:, None] * d[None, :]
    delta = samples[:, None, :] - pa[None, :, :]
    t = np.clip(np.sum(delta * v[None, :, :], axis=2) / v2[None, :], 0.0, 1.0)
    nearest = pa[None, :, :] + t[:, :, None] * v[None, :, :]
    dist = np.hypot(samples[:, None, 0] - nearest[:, :, 0],
                    samples[:, None, 1] - nearest[:, :, 1])
    parallel = np.abs(unit @ tangent) >= PARAPET_PARALLEL_DOT
    matches = (dist <= PARAPET_DUPLICATE_M) & parallel[None, :]
    if level is not None:
        parapet_z = segments["za"][box][None, :] + t * (segments["zb"][box] - segments["za"][box])[None, :]
        matches &= np.isfinite(parapet_z) & (np.abs(parapet_z - float(level)) <= PARAPET_LEVEL_TOLERANCE_M)
    return float(matches.any(axis=1).mean()) >= PARAPET_MATCH_SHARE


def forest_rows(frame, infra_fn, exact, elev, decks=(), parapets=()):
    """Return Forest-item rows for the stock Italy guardrail model.

    Row layout matches trees.lua:
      [group, x, y, z, yaw, unused, height, rank, forwardXYZ, backXYZ, upXYZ]
    The three basis vectors are stored consecutively and installed as MatrixF columns. This lets
    each 3 m module follow the surface's pitch rather than looking like stairs on a grade.
    Groups: rail, start/end caps, then their reverse-facing equivalents for two_sided=yes.
    """
    if infra_fn is None:
        return []
    parapet_segments = _parapet_segments(parapets)
    x0, y0, x1, y1 = map(float, exact)
    corners_x = np.array([x0, x0, x1, x1], np.float64)
    corners_y = np.array([y0, y1, y0, y1], np.float64)
    la, lo, _ = frame.enu_to_geodetic(corners_x, corners_y, np.zeros(4, np.float64))
    try:
        keys = mapcells.cells_for_points(la, lo, pad_lat=0.00005, pad_lon=0.00005, max_cells=64)
    except (ValueError, OverflowError):
        return []

    seen = set()
    placements = []
    for key in keys:
        try:
            feats = infra_fn(key)
        except Exception as ex:
            log.debug("guardrails: infrastructure cell %s unavailable: %s", key, ex)
            continue
        if feats is None or not len(feats):
            continue
        if feats.attrs.get("class") is None and feats.attrs.get("tags") is None:
            continue
        boxes = feats.boxes(frame)
        for i in range(len(feats)):
            fid = str(feats.ids[i])
            if fid in seen or int(feats.kind[i]) != mapgeom.LINE or not _is_guardrail(feats, i):
                continue
            b = boxes[i]
            if (b[2] < x0 - BBOX_PAD_M or b[0] > x1 + BBOX_PAD_M or
                    b[3] < y0 - BBOX_PAD_M or b[1] > y1 + BBOX_PAD_M):
                continue
            seen.add(fid)
            two_sided = _is_two_sided(feats, i)
            deck_hint = _deck_level_hint(feats, i)
            for part_no, part in enumerate(feats.parts(i)):
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
                arc = np.r_[0.0, np.cumsum(np.hypot(*np.diff(line, axis=0).T))]
                total = float(arc[-1])
                if total < 1.0:
                    continue

                # Fit all modules inside the mapped line. Neighbouring 3 m shapes overlap by 0.2 m;
                # the unused margins are shared between the two ends, avoiding gaps at either cap.
                count = max(1, int(math.floor((total - MODULE_LENGTH_M) / MODULE_STEP_M)) + 1)
                covered = MODULE_LENGTH_M + (count - 1) * MODULE_STEP_M
                start = 0.5 * (total - covered)

                spans = []
                for k in range(count):
                    sa = start + k * MODULE_STEP_M
                    sb = sa + MODULE_LENGTH_M
                    a, _ = _at(line, arc, float(sa))
                    b, _ = _at(line, arc, float(sb))
                    if float(np.hypot(*(b - a))) < 0.05:
                        continue
                    p = 0.5 * (a + b)
                    rank_bytes = hashlib.blake2b(f"{fid}:{part_no}:{k}".encode("utf-8"), digest_size=4).digest()
                    rank = int.from_bytes(rank_bytes, "big") / 4294967296.0
                    spans.append((k, a, b, p, rank))

                if not spans:
                    continue
                _, first_a, first_b, _, first_rank = spans[0]
                _, last_a, last_b, _, last_rank = spans[-1]
                candidates = []
                for k, a, b, p, rank in spans:
                    if x0 <= p[0] < x1 and y0 <= p[1] < y1:
                        candidates.append((0, p, a, b, rank, False))
                        if two_sided:
                            candidates.append((3, p, a, b, rank, True))
                # Caps extend outwards from the ends. Reverse-facing cap types are swapped so they
                # also flare outwards rather than into the run.
                if x0 <= first_a[0] < x1 and y0 <= first_a[1] < y1:
                    candidates.append((1, first_a, first_a, first_b, first_rank, False))
                    if two_sided:
                        candidates.append((4, first_a, first_a, first_b, first_rank, True))
                if x0 <= last_b[0] < x1 and y0 <= last_b[1] < y1:
                    candidates.append((2, last_b, last_a, last_b, last_rank, False))
                    if two_sided:
                        candidates.append((5, last_b, last_a, last_b, last_rank, True))

                for group, p, a, b, rank, reverse in candidates:
                    # Match the original mapped line before offsets for two-sided models. Otherwise
                    # a central separator near the inner parapets could be shifted onto the match band.
                    # Keep the source segment and check height later so a lower road beneath a bridge
                    # is not mistaken for the bridge's own guardrail.
                    source_a, source_b = a, b
                    parapet_xy_match = _overlaps_bridge_parapet(source_a, source_b, parapet_segments)
                    if two_sided:
                        # The model's visible side is opposite its back axis. Keep the backs
                        # toward the centre gap and the visible faces outward on both sides.
                        tangent = np.asarray(b, np.float64) - np.asarray(a, np.float64)
                        tangent_len = float(np.hypot(tangent[0], tangent[1]))
                        if tangent_len >= 1e-6:
                            normal = np.array([-tangent[1], tangent[0]], np.float64) / tangent_len
                            side = 1.0 if reverse else -1.0
                            offset = normal * (0.5 * TWO_SIDED_SPACING_M * side)
                            p, a, b = p + offset, a + offset, b + offset
                    placements.append((group, p, a, b, rank, reverse, parapet_xy_match,
                                       source_a, source_b, deck_hint))

    if not placements:
        return []

    # One batched terrain/deck query for all module origins and their grade endpoints.
    sample_points, sample_deck_hints = [], []
    for _, p, a, b, _, _, _, _, _, deck_hint in placements:
        sample_points.extend((p, a, b))
        sample_deck_hints.extend((deck_hint, deck_hint, deck_hint))
    heights = _surface_z(frame, np.asarray(sample_points, np.float64), elev, decks,
                         deck_preference=sample_deck_hints)
    rows = []
    for i, (group, p, a, b, rank, reverse, parapet_xy_match,
            source_a, source_b, _deck_hint_value) in enumerate(placements):
        z, za, zb = (float(heights[3 * i]), float(heights[3 * i + 1]), float(heights[3 * i + 2]))
        if parapet_xy_match and _overlaps_bridge_parapet(source_a, source_b, parapet_segments, level=z):
            continue
        forward = np.array([float(b[0] - a[0]), float(b[1] - a[1]), zb - za], np.float64)
        length = float(np.linalg.norm(forward))
        if length < 1e-6:
            continue
        forward /= length
        up = np.array([0.0, 0.0, 1.0], np.float64) - forward[2] * forward
        up_len = float(np.linalg.norm(up))
        if up_len < 1e-6:
            up = np.array([0.0, 1.0, 0.0], np.float64)
        else:
            up /= up_len
        back = np.cross(up, forward)
        back_len = float(np.linalg.norm(back))
        if back_len < 1e-6:
            continue
        back /= back_len
        if reverse:
            forward, back = -forward, -back
        yaw = math.degrees(math.atan2(float(forward[1]), float(forward[0])))
        rows.append([group, float(p[0]), float(p[1]), z, yaw, 0.0,
                     MODULE_HEIGHT_M, rank,
                     float(forward[0]), float(forward[1]), float(forward[2]),
                     float(back[0]), float(back[1]), float(back[2]),
                     float(up[0]), float(up[1]), float(up[2])])
    return rows