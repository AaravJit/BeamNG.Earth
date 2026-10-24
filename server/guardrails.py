"""Place mapped Overture guard rails using the stock Italy guardrail Forest models.

Only mapped guard rails are rendered. Their Forest rows reuse the game's model batching
and near-field collision path instead of creating a custom mesh for every road tile.
"""
import hashlib
import json
import logging
import math

import numpy as np

import mapcells
import mapgeom

log = logging.getLogger("earthd.guardrails")

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


# TEMPORARY: suppress every mapped guardrail within 5 m of a bridge footprint.
# The deck width is included so this buffer starts at the bridge edges, not its centreline.
BRIDGE_GUARDRAIL_CLEARANCE_M = 5.0


def _bridge_buffer_segments(decks, clearance=BRIDGE_GUARDRAIL_CLEARANCE_M):
    """Flatten bridge deck profiles once, reusing their segment buffers for every rail module."""
    starts, ends, directions, lengths2, radii = [], [], [], [], []
    for deck in decks or ():
        line = np.asarray(deck.get("n", ()), np.float64)
        if len(line) < 2 or line.ndim != 2 or line.shape[1] < 2:
            continue
        a, b = line[:-1, :2], line[1:, :2]
        d = b - a
        l2 = np.sum(d * d, axis=1)
        keep = l2 > 1e-12
        if not keep.any():
            continue
        width = float(deck.get("w", 0.0))
        if not np.isfinite(width):
            width = 0.0
        radius = max(0.0, float(clearance)) + max(0.0, width) / 2.0
        starts.append(a[keep])
        ends.append(b[keep])
        directions.append(d[keep])
        lengths2.append(l2[keep])
        radii.append(np.full(int(keep.sum()), radius, np.float64))
    if not starts:
        return None

    a, b = np.concatenate(starts), np.concatenate(ends)
    radius = np.concatenate(radii)
    return {
        "a": a,
        "b": b,
        "d": np.concatenate(directions),
        "l2": np.concatenate(lengths2),
        "radius": radius,
        "lo": np.minimum(a, b) - radius[:, None],
        "hi": np.maximum(a, b) + radius[:, None],
    }


def _within_bridge_buffer(a, b, decks, clearance=BRIDGE_GUARDRAIL_CLEARANCE_M):
    """Whether an XY guardrail segment lies within clearance of any bridge footprint.

    The caller can pass pre-flattened segments, which avoids rebuilding the same arrays
    hundreds of times while a tile's modules are generated.
    """
    if isinstance(decks, dict) and {"a", "b", "d", "l2", "radius", "lo", "hi"} <= decks.keys():
        segments = decks
    else:
        segments = _bridge_buffer_segments(decks, clearance)
    if segments is None:
        return False

    a, b = np.asarray(a, np.float64).reshape(2), np.asarray(b, np.float64).reshape(2)
    r = b - a
    r2 = float(np.dot(r, r))
    qlo, qhi = np.minimum(a, b), np.maximum(a, b)
    box = ((segments["hi"][:, 0] >= qlo[0]) & (segments["lo"][:, 0] <= qhi[0]) &
           (segments["hi"][:, 1] >= qlo[1]) & (segments["lo"][:, 1] <= qhi[1]))
    if not box.any():
        return False

    starts, ends = segments["a"][box], segments["b"][box]
    d, l2, radius = segments["d"][box], segments["l2"][box], segments["radius"][box]
    radius2 = radius * radius

    # Guardrail endpoints to bridge segments.
    for p in (a, b):
        t = np.clip(np.sum((p - starts) * d, axis=1) / l2, 0.0, 1.0)
        near = starts + t[:, None] * d
        if np.any(np.sum((near - p) ** 2, axis=1) <= radius2):
            return True

    if r2 <= 1e-12:
        return False

    # Bridge endpoints to the guardrail segment.
    for p in (starts, ends):
        t = np.clip(np.sum((p - a) * r, axis=1) / r2, 0.0, 1.0)
        near = a + t[:, None] * r
        if np.any(np.sum((near - p) ** 2, axis=1) <= radius2):
            return True

    # Handle crossings where both segment endpoints are outside the buffer.
    qmp = starts - a
    denom = r[0] * d[:, 1] - r[1] * d[:, 0]
    nonparallel = np.abs(denom) > 1e-10
    if nonparallel.any():
        cross_q_d = qmp[:, 0] * d[:, 1] - qmp[:, 1] * d[:, 0]
        cross_q_r = qmp[:, 0] * r[1] - qmp[:, 1] * r[0]
        t = np.zeros_like(denom)
        u = np.zeros_like(denom)
        t[nonparallel] = cross_q_d[nonparallel] / denom[nonparallel]
        u[nonparallel] = cross_q_r[nonparallel] / denom[nonparallel]
        if np.any(nonparallel & (t >= 0.0) & (t <= 1.0) & (u >= 0.0) & (u <= 1.0)):
            return True
    return False



def _at(line, arc, distance):
    """Point and unit tangent at arc length distance along a 2D polyline."""
    k = min(max(int(np.searchsorted(arc, distance, side="right") - 1), 0), len(line) - 2)
    span = max(float(arc[k + 1] - arc[k]), 1e-9)
    t = float(np.clip((distance - arc[k]) / span, 0.0, 1.0))
    d = line[k + 1] - line[k]
    length = max(float(np.hypot(d[0], d[1])), 1e-9)
    return line[k] + t * d, d / length


DECK_AUTO_SNAP_TOLERANCE_M = 1.5  # Untagged rails snap to a deck only when terrain agrees.


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
# Tight matching is used for all parapets; a wider tolerance is safe only for a parapet
# proven to be on the OUTSIDE of a bridge. This avoids deleting the centre divider between decks.
PARAPET_DUPLICATE_M = 0.7
OUTER_PARAPET_DUPLICATE_M = 2.0
PARAPET_LEVEL_TOLERANCE_M = 1.0
PARAPET_PARALLEL_DOT = 0.75
PARAPET_MATCH_SHARE = 0.75
PARAPET_MATCH_SAMPLES = 7
OUTER_DECK_SEARCH_M = 30.0


def _deck_is_outward_of_parapet(mid, tangent, outward, z, decks):
    """True when another same-level, parallel bridge deck lies outside this concrete parapet."""
    for deck in decks or ():
        line = np.asarray(deck.get("n", ()), np.float64)
        if len(line) < 2 or line.shape[1] < 3:
            continue
        a, b = line[:-1, :2], line[1:, :2]
        v = b - a
        l2 = np.maximum(np.sum(v * v, axis=1), 1e-12)
        t = np.clip(np.sum((mid - a) * v, axis=1) / l2, 0.0, 1.0)
        near = a + t[:, None] * v
        delta = near - mid
        signed = delta @ outward
        dist = np.hypot(delta[:, 0], delta[:, 1])
        length = np.maximum(np.linalg.norm(v, axis=1), 1e-9)
        parallel = np.abs((v / length[:, None]) @ tangent) >= PARAPET_PARALLEL_DOT
        deck_z = line[:-1, 2] + t * (line[1:, 2] - line[:-1, 2])
        level_ok = np.abs(deck_z - z) <= PARAPET_LEVEL_TOLERANCE_M
        if np.any(parallel & level_ok & (signed >= 1.0) &
                  (signed <= OUTER_DECK_SEARCH_M) & (dist <= OUTER_DECK_SEARCH_M)):
            return True
    return False


def _parapet_segments(parapets, decks=()):
    """Flatten generated bridge-parapet centre lines and identify inner versus outer walls.

    The parapet's outward normal points away from its own deck. If another parallel, same-level
    bridge deck lies in that direction, this is an inner parapet facing the central divider; only
    the tighter duplicate tolerance may match it. Outer parapets may replace mapped roadside rails
    that were mapped a little farther from the wall than its centre line.
    """
    starts, ends, z_starts, z_ends, outer_segments = [], [], [], [], []
    for parapet in parapets or ():
        line = np.asarray(parapet.get("line", ()) if isinstance(parapet, dict) else parapet,
                          np.float64).reshape(-1, 2)
        if len(line) < 2:
            continue
        z = np.asarray(parapet.get("z", ()) if isinstance(parapet, dict) else (), np.float64).reshape(-1)
        if len(z) != len(line):
            z = np.full(len(line), np.nan, np.float64)
        out = np.asarray(parapet.get("out", ()) if isinstance(parapet, dict) else (),
                         np.float64).reshape(-1, 2)
        if len(out) != len(line):
            out = np.zeros_like(line)
        a, b = line[:-1], line[1:]
        d = b - a
        length = np.hypot(d[:, 0], d[:, 1])
        keep = length > 1e-6
        if not keep.any():
            continue
        unit = d[keep] / length[keep, None]
        mids = (a[keep] + b[keep]) * 0.5
        normals = out[:-1][keep] + out[1:][keep]
        nl = np.linalg.norm(normals, axis=1)
        normals[nl > 1e-9] /= nl[nl > 1e-9, None]
        is_outer = []
        segment_z = (z[:-1][keep] + z[1:][keep]) * 0.5
        for mid, tangent, outward, level in zip(mids, unit, normals, segment_z):
            if np.linalg.norm(outward) < 1e-9 or not np.isfinite(level):
                is_outer.append(False)
                continue
            inner = _deck_is_outward_of_parapet(mid, tangent, outward, float(level), decks)
            is_outer.append(not inner)
        starts.append(a[keep])
        ends.append(b[keep])
        z_starts.append(z[:-1][keep])
        z_ends.append(z[1:][keep])
        outer_segments.append(np.asarray(is_outer, bool))
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
        "outer": np.concatenate(outer_segments),
    }


def _overlaps_bridge_parapet(a, b, segments, level=None, distance_limit=PARAPET_DUPLICATE_M,
                             outer_only=False):
    """Whether a module runs along a generated concrete parapet, not merely across it."""
    if segments is None:
        return False
    a, b = np.asarray(a, np.float64), np.asarray(b, np.float64)
    d = b - a
    length = float(np.hypot(d[0], d[1]))
    if length < 1e-6:
        return False
    tangent = d / length
    lo = np.minimum(a, b) - distance_limit
    hi = np.maximum(a, b) + distance_limit
    box = ((segments["hi"][:, 0] >= lo[0]) & (segments["lo"][:, 0] <= hi[0]) &
           (segments["hi"][:, 1] >= lo[1]) & (segments["lo"][:, 1] <= hi[1]))
    if outer_only:
        box &= segments["outer"]
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
    matches = (dist <= distance_limit) & parallel[None, :]
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
    bridge_buffer = _bridge_buffer_segments(decks)
    parapet_segments = _parapet_segments(parapets, decks)
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
                    # Temporary blanket exclusion: remove all mapped rails (including centre dividers)
                    # within 5 m of any bridge footprint while the bridge parapets are being reworked.
                    if _within_bridge_buffer(source_a, source_b, bridge_buffer):
                        continue
                    parapet_xy_match = _overlaps_bridge_parapet(source_a, source_b, parapet_segments)
                    if not parapet_xy_match:
                        parapet_xy_match = _overlaps_bridge_parapet(
                            source_a, source_b, parapet_segments,
                            distance_limit=OUTER_PARAPET_DUPLICATE_M, outer_only=True)
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
        if parapet_xy_match and (
                _overlaps_bridge_parapet(source_a, source_b, parapet_segments, level=z) or
                _overlaps_bridge_parapet(source_a, source_b, parapet_segments, level=z,
                                         distance_limit=OUTER_PARAPET_DUPLICATE_M, outer_only=True)):
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