"""Origin-independent tiles ("geotiles", .claude/work/geotiles_plan.md): glue over earthfast's geotiles.rs.

Content that does not depend on the anchor (trees, bushes, rocks first: vegetation.py; road content next) is made
once per tile of a global Web-Mercator grid at zoom Z (17: ~240 m at 39 deg, 8 x 8 of them in a map cell), in the
tile's own frame: the ENU tangent plane at its centre, in the world convention (x, y of a place at h = 0, z at its
height). Its key ("g17_x_y") never names an anchor, so a tile the mod has stays valid across rebases: the mod places it
into the current anchor's world (geotile.lua placement: the rigid move made exact at the tile's pivot, plus the shear
that carries the height difference from it) instead of the server building it again.

Planning frames (grading's plans, bridge / tunnel chain profiles) are the frames of zoom PLAN_Z regions (10: ~30 km),
the same way: one plan per region whatever the anchor.
"""
import functools

import numpy as np

import fast
import geodesy

Z = 17                  # content tiles (the mod's hello cap geoTiles = Z)
PLAN_Z = 10             # planning regions (grading)
CHAIN_Z = 12            # bridge / tunnel chain profile stores (~8 km: a deck solved in a nearly level frame)
NEED = 103              # earthfast's geotiles kernels


def _ef():
    fast.require(NEED, "geotiles")
    return fast._ef


def tile_of(lat, lon, z=Z):
    """(x, y) of the zoom-z tile under each point (arrays in, int64 arrays out; scalars in, ints out)."""
    la, lo = np.atleast_1d(np.asarray(lat, np.float64)), np.atleast_1d(np.asarray(lon, np.float64))
    xs, ys = _ef().geo_tile_of(np.ascontiguousarray(la.ravel()), np.ascontiguousarray(lo.ravel()), int(z))
    if np.ndim(lat) == 0 and np.ndim(lon) == 0:
        return int(xs[0]), int(ys[0])
    return xs.reshape(la.shape), ys.reshape(la.shape)


def key(x, y, z=Z):
    """The tile's key in messages and caches: 'g<z>_<x>_<y>'."""
    return f"g{int(z)}_{int(x)}_{int(y)}"


def parse(k):
    """(z, x, y) of a key, or None."""
    try:
        zz, x, y = str(k).split("_")
        if not zz.startswith("g"):
            return None
        return int(zz[1:]), int(x), int(y)
    except (ValueError, AttributeError):
        return None


@functools.lru_cache(maxsize=65536)
def info(x, y, z=Z):
    """(centre lat, centre lon, south, west, north, east) of a tile."""
    return tuple(float(v) for v in _ef().geo_tile_info(int(z), int(x), int(y)))


def centre(x, y, z=Z):
    return info(x, y, z)[:2]


class WorldFrame(geodesy.EnuFrame):
    """An EnuFrame whose enu_to_geodetic(e, n, 0) is the exact inverse of the world convention (the place whose h = 0
    point has these e, n; geotiles.rs world_to_geodetic), not the plane point (e, n, 0), which lies d^2 / 2R over the
    ellipsoid: a place's lat / lon read back from its own x, y was d^3 / 2R^2 off (4 cm at 15 km, 25 cm at a planning
    region's corner). The frames content and plans are made in: what they sample is where their vertices are."""

    def enu_to_geodetic(self, e, n, u=0.0):
        if np.ndim(u) != 0 or float(u) != 0.0:
            return super().enu_to_geodetic(e, n, u)
        E, N = np.broadcast_arrays(np.asarray(e, np.float64), np.asarray(n, np.float64))
        shape = E.shape
        p = np.zeros((E.size, 3))
        p[:, 0], p[:, 1] = E.ravel(), N.ravel()
        la, lo, _ = _ef().geo_world_to_geodetic(float(self.lat0), float(self.lon0), p)
        if shape == ():
            return np.float64(la[0]), np.float64(lo[0]), np.float64(0.0)
        return la.reshape(shape), lo.reshape(shape), np.zeros(shape)


@functools.lru_cache(maxsize=4096)
def frame(x, y, z=Z):
    """The tile's frame: a WorldFrame at its centre (h = 0)."""
    la, lo = centre(x, y, z)
    return WorldFrame(la, lo)


def quad(anchor, x, y, z=Z):
    """The tile's corners (4, 2: SW, SE, NE, NW) in the world of anchor (an EnuFrame)."""
    return _ef().geo_tile_quad(float(anchor.lat0), float(anchor.lon0), int(z), int(x), int(y))


def tiles_near(anchor, x, y, r, z=Z):
    """[(tx, ty, m)] of the tiles whose quad in anchor's world comes within r of (x, y), nearest first."""
    return _ef().geo_tiles_near(float(anchor.lat0), float(anchor.lon0), int(z), float(x), float(y), float(r))


def _pts(p):
    p = np.asarray(p, np.float64)
    if p.ndim == 1:
        p = p[None]
    return np.ascontiguousarray(p)


def world_map(src, dst, pts):
    """Points (n, 2 or 3) of src's world (EnuFrame) in dst's world, exactly (n, 3)."""
    return _ef().geo_world_map(float(src.lat0), float(src.lon0), float(dst.lat0), float(dst.lon0), _pts(pts))


def world_xy_map(src, dst, xy):
    """Ground points (n, 2) at h = 0 of src's world in dst's world (n, 2): the plans' and outlines' vertices."""
    xy = np.asarray(xy, np.float64)
    if src is dst or (src.lat0 == dst.lat0 and src.lon0 == dst.lon0):
        return xy[:, :2].copy()
    if not len(xy):
        return np.zeros((0, 2))
    p = np.zeros((len(xy), 3))
    p[:, :2] = xy[:, :2]
    out = world_map(src, dst, p)[:, :2]
    return np.ascontiguousarray(out)


def world_to_geodetic(f, pts):
    """(lat, lon, h) of points (n, 3) of f's world (the exact inverse of geodetic_to_world)."""
    return _ef().geo_world_to_geodetic(float(f.lat0), float(f.lon0), _pts(pts))


def geodetic_to_world(f, lat, lon, h=0.0):
    """(x, y, z) arrays: x, y of each place at h = 0, z at its height h."""
    x, y, _ = f.geodetic_to_enu(lat, lon, 0.0)
    _, _, z = f.geodetic_to_enu(lat, lon, h)
    return x, y, z


def placement(src, dst, pivot):
    """(R (3, 3), t (3,), c (3,)): src's world placed in dst's at pivot (src world): p' = R p + t + c (p.z - pivot.z)."""
    r, t, c = _ef().geo_placement(float(src.lat0), float(src.lon0), float(dst.lat0), float(dst.lon0),
                                  float(pivot[0]), float(pivot[1]), float(pivot[2]))
    return np.array(r, np.float64).reshape(3, 3), np.array(t, np.float64), np.array(c, np.float64)


def place(src, dst, pivot, pts):
    """pts (n, 3) of src's world by placement (the mod's way: geotile.lua M.apply)."""
    r, t, c = placement(src, dst, pivot)
    p = _pts(pts)
    return p @ r.T + t + np.outer(p[:, 2] - float(pivot[2]), c)


# --- planning regions ---------------------------------------------------------------------------------------------

def region_of(lat, lon):
    """The planning region (x, y) at PLAN_Z of a point."""
    return tile_of(lat, lon, PLAN_Z)


def region_of_cell(cell):
    """The planning region of a map cell (mapcells.CellKey, zoom >= PLAN_Z: nested, exactly)."""
    z, x, y = int(cell[0]), int(cell[1]), int(cell[2])
    sh = z - PLAN_Z
    if sh < 0:
        raise ValueError(f"geotiles: cell {cell} is coarser than the planning regions")
    return x >> sh, y >> sh


def region_frame(rx, ry):
    """The region's planning frame (EnuFrame at its centre)."""
    return frame(rx, ry, PLAN_Z)


def tile_cell(x, y, z=Z, cell_z=14):
    """The map cell (z, x, y) a tile lies in (nested: zoom z >= cell_z)."""
    sh = int(z) - int(cell_z)
    return (int(cell_z), int(x) >> sh, int(y) >> sh)
