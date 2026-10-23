"""Gas stations with refuelling (the user, 2026-10-23: "the refueling is the main part, we can already refuel in beam
so I want to include it on this impressive map mod"; design: .claude/superpowers/specs/2026-10-23-gas-stations-design.md).

Stations come from Overture places (mapdata layer "places"): a place is a fuel-station candidate by its category
(gas_station, fueling_station) or by its brand's Wikidata id, from two lists (the user agreed to two, 2026-10-23):
fuel brands count on their own; convenience brands that sell fuel at some stores only (Sheetz, Wawa, 7-Eleven,
Circle K ...) count once there is more evidence, a canopy found by find_canopy or a merged gas_station pin, so a plain
convenience store never gets pumps in its car park. Pins of one station from several sources (meta, Microsoft,
AllThePlaces, DAC: 10-60 m apart around Pittsburgh) are merged.

The pins are not on the pumps (the user checked three on satellite view: on the shop, beside the pumps, on the road),
so a station's layout comes from the buildings and roads around it, not from the pin. Pure (numpy), deterministic: ids
are hashes of the Overture ids, so every tile, anchor and BeamMP peer places the same station.
"""
import hashlib
import logging
import re
from dataclasses import dataclass

import numpy as np

log = logging.getLogger("earthd.gasstations")

GAS_CATEGORIES = {"gas_station", "fueling_station"}
CLOSED = {"closed", "permanently_closed", "temporarily_closed"}

# Wikidata ids as Overture's places carry them (brand.wikidata, release 2026-09-23.1, read 2026-10-23 in boxes around
# Pennsylvania/Ohio, Texas, Germany, England, Tokyo, Sydney and Sao Paulo). Fuel brands: a match is a station.
FUEL_BRANDS = {
    "Q458363": "Marathon", "Q1423218": "Sunoco", "Q109675651": "Exxon", "Q109676002": "Mobil", "Q152057": "BP",
    "Q5617505": "Gulf", "Q108477252": "Kwik Fill", "Q304769": "Arco", "Q1283291": "Valero", "Q319642": "Chevron",
    "Q775060": "Texaco", "Q735942": "RaceTrac", "Q19604459": "Murphy Express", "Q109341187": "Conoco",
    "Q1658320": "76", "Q1656230": "Phillips 66", "Q110716465": "Shell", "Q7575683": "Speedway",
    "Q7374169": "Royal Farms", "Q4982335": "Buc-ee's", "Q64149010": "Maverik", "Q7796584": "Thorntons",
    "Q7835892": "TA Express", "Q139895750": "Love's", "Q565734": "Aral", "Q154037": "TotalEnergies",
    "Q867662": "Esso", "Q1009104": "bft", "Q565594": "Eni", "Q377915": "Agip", "Q300147": "Avia", "Q568940": "JET",
    "Q2007561": "OIL!", "Q2121146": "Q1", "Q16998281": "Murco", "Q130223576": "EG On the Move",
    "Q1640290": "ENEOS",
}
# Convenience brands with fuel at some of their stores: a match needs a canopy or a gas_station pin too.
CONV_BRANDS = {
    "Q7492551": "Sheetz", "Q5936320": "Wawa", "Q259340": "7-Eleven", "Q3268010": "Circle K",
    "Q2940968": "Casey's", "Q110278299": "Bonjour",
}


_FUEL_NAMES = {v.lower() for v in FUEL_BRANDS.values()}
_CONV_NAMES = {v.lower() for v in CONV_BRANDS.values()}


def classify(category, taxonomy, wikidata, status, brand_name=""):
    """(gas, fuel_brand, conv_brand) of a place, or None when it is no candidate (or closed). Without a brand Wikidata
    id the brand's name, whole, against the lists' names (a Sheetz near Pittsburgh: "Sheetz", no id)."""
    if status is not None and str(status) in CLOSED:
        return None
    gas = str(category or "") in GAS_CATEGORIES or str(taxonomy or "") in GAS_CATEGORIES
    wd = str(wikidata or "")
    if wd:
        fuel, conv = wd in FUEL_BRANDS, wd in CONV_BRANDS
    else:
        bn = str(brand_name or "").strip().lower()
        fuel, conv = bn in _FUEL_NAMES, bn in _CONV_NAMES
    if not (gas or fuel or conv):
        return None
    return gas, fuel, conv


@dataclass(frozen=True)
class Pin:
    id: str
    x: float            # anchor frame, m
    y: float
    name: str
    brand: str
    wikidata: str
    gas: bool
    fuel_brand: bool
    conv_brand: bool


@dataclass(frozen=True)
class Station:
    id: str
    x: float
    y: float
    name: str
    brand: str
    pins: tuple
    confirmed: bool     # a gas_station pin or a fuel brand; else a convenience brand that needs a canopy


def pins(frame, layers):
    """Pins of the fuel-station candidates among places layers (mapgeom.Features, one per map cell; a place in two
    cells is taken once, by id), in frame (an EnuFrame)."""
    out, seen = [], set()
    for f in layers:
        if f is None or len(f) == 0:
            continue
        a = f.attrs

        def col(k, i):
            v = a[k][i] if k in a else None
            return "" if v is None else str(v)
        xy = None
        for i in range(len(f)):
            fid = str(f.ids[i])
            if fid in seen:
                continue
            c = classify(col("basic_category", i), col("taxonomy", i), col("brand_wikidata", i),
                         col("operating_status", i) or None, col("brand_name", i))
            if c is None:
                continue
            seen.add(fid)
            if xy is None:
                xy = f.xy_in(frame)
            k = f.coord_off[f.ring_off[f.part_off[i]]]
            out.append(Pin(fid, float(xy[k, 0]), float(xy[k, 1]), col("name", i), col("brand_name", i),
                           col("brand_wikidata", i), *c))
    return out


MERGE_M = 60.0      # pins of one station this close together (the Speedway's two: 55 m)


def _tokens(name):
    return {t for t in re.findall(r"[a-z]+", name.lower()) if len(t) >= 3}


def _compatible(a, b):
    if a.wikidata and b.wikidata:
        return a.wikidata == b.wikidata
    if not (a.wikidata or a.brand) or not (b.wikidata or b.brand):
        return True
    return bool(_tokens(a.name + " " + a.brand) & _tokens(b.name + " " + b.brand))


def merge(ps, merge_m=MERGE_M):
    """Stations of pins ps: single linkage of compatible pins within merge_m (the same brand id, a shared name word,
    or one of them without a brand)."""
    ps = sorted(ps, key=lambda p: p.id)
    parent = list(range(len(ps)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(ps)):
        for j in range(i + 1, len(ps)):
            a, b = ps[i], ps[j]
            if (a.x - b.x) ** 2 + (a.y - b.y) ** 2 <= merge_m ** 2 and _compatible(a, b):
                parent[root(j)] = root(i)
    groups = {}
    for i in range(len(ps)):
        groups.setdefault(root(i), []).append(ps[i])
    out = []
    for g in groups.values():
        ids = sorted(p.id for p in g)
        named = max(g, key=lambda p: (bool(p.brand or p.wikidata), p.gas))     # (a branded pin, else a gas one)
        out.append(Station(hashlib.blake2b("|".join(ids).encode(), digest_size=6).hexdigest(),
                           float(np.mean([p.x for p in g])), float(np.mean([p.y for p in g])),
                           named.name or named.brand, named.brand or FUEL_BRANDS.get(named.wikidata)
                           or CONV_BRANDS.get(named.wikidata, ""), tuple(ids),
                           any(p.gas or p.fuel_brand for p in g)))
    return sorted(out, key=lambda s: s.id)


# --- the shop and the canopy -------------------------------------------------------------------------------------
@dataclass(frozen=True, eq=False)
class Bld:
    id: str
    ring: np.ndarray    # (k, 2) anchor frame, closed
    cls: str
    name: str
    src: str
    height: float | None


SHOP_SEARCH_M = 60.0        # the shop: a building this close to the station's pins
CANOPY_SEARCH_M = 50.0      # the canopy: this close to the shop (centre to centre)
ML_SOURCES = {"Microsoft ML Buildings", "Google Open Buildings"}    # traced from imagery, never tagged by a mapper
CANOPY_RECT_MIN = 0.85      # area / its oriented box's: a canopy is a rectangle
CANOPY_AREA_M2 = (120.0, 900.0)
CANOPY_MAX_H_M = 7.0
ROOF_CLASSES = {"roof"}     # OSM building=roof (the Speedway's canopy: outbuilding / roof)
ROAD_SLACK_M = 20.0         # a canopy may face any public road this much farther from the shop than the nearest (a
                            # corner lot: a Sheetz near Pittsburgh faces the road 59 m away, the nearest is 49 m)


def _area(ring):
    x, y = ring[:, 0], ring[:, 1]
    return 0.5 * abs(float(np.dot(x[:-1], y[1:]) - np.dot(y[:-1], x[1:])))


def _centre(ring):
    return ring[:-1].mean(axis=0)


def _obox(ring):
    import buildings
    c, u, hl, hw = buildings.oriented_box(ring[:-1] if len(ring) > 1 and np.allclose(ring[0], ring[-1]) else ring)
    return np.asarray(c, np.float64), np.asarray(u, np.float64), float(hl), float(hw)


def _nearest_on(lines, p):
    """(point, distance, line index) of the drive lines' centreline point nearest p."""
    best = (None, np.inf, -1)
    for k, (pts, _) in enumerate(lines):
        a, b = pts[:-1], pts[1:]
        d = b - a
        L2 = np.maximum((d * d).sum(1), 1e-12)
        t = np.clip(((p - a) * d).sum(1) / L2, 0.0, 1.0)
        q = a + d * t[:, None]
        dist = np.hypot(*(q - p).T)
        i = int(np.argmin(dist))
        if dist[i] < best[1]:
            best = (q[i], float(dist[i]), k)
    return best


def _named(b, st):
    words = _tokens(st.name + " " + st.brand)
    return bool(words) and bool(_tokens(b.name) & words)


def find_shop(st, blds):
    """The station's shop: among the buildings (not roofs) within SHOP_SEARCH_M of the station, one named like the
    station, else the nearest a mapper drew, else the nearest imagery outline (one of those next to a station is often
    its canopy: a Sunoco near Pittsburgh, its pin on the road, 2026-10-23); None without one."""
    p = np.array([st.x, st.y])
    near = []
    for b in blds:
        if b.cls in ROOF_CLASSES:
            continue
        d = float(np.hypot(*(_centre(b.ring) - p)))
        if d <= SHOP_SEARCH_M:
            near.append((not _named(b, st), b.src in ML_SOURCES, d, b.id, b))
    return min(near, key=lambda r: r[:4])[4] if near else None


def find_canopy(st, shop, blds, roads):
    """The station's canopy among blds, or None (then no building is touched): the nearest tagged roof within
    CANOPY_SEARCH_M of the shop; else an untagged imagery outline (ML_SOURCES) that is not the shop, a canopy-sized
    rectangle no taller than CANOPY_MAX_H_M, between the shop and a public road (projected on the line from the
    shop's centre to that road's nearest point; the nearest road or one at most ROAD_SLACK_M farther), and the only one
    that passes (the user, 2026-10-23, on turning a car wash into a
    canopy: "I wouldn't like that")."""
    sc = _centre(shop.ring)
    roofs = [(float(np.hypot(*(_centre(b.ring) - sc))), b.id, b) for b in blds
             if b is not shop and b.cls in ROOF_CLASSES]
    roofs = [r for r in roofs if r[0] <= CANOPY_SEARCH_M]
    if roofs:
        return min(roofs, key=lambda r: r[:2])[2]
    fronts = [_nearest_on([rl], sc) for rl in roads]
    fronts = [(q, d) for q, d, _ in fronts if q is not None and d >= 1.0]
    if not fronts:
        return None
    dmin = min(d for _, d in fronts)
    fronts = [(q, d) for q, d in fronts if d <= dmin + ROAD_SLACK_M]
    _, _, shl, _ = _obox(shop.ring)
    found = []
    for b in blds:
        if b is shop or b.cls or b.name or b.src not in ML_SOURCES:
            continue
        if b.height is not None and np.isfinite(b.height) and b.height > CANOPY_MAX_H_M:
            continue
        c = _centre(b.ring)
        if float(np.hypot(*(c - sc))) > CANOPY_SEARCH_M:
            continue
        area = _area(b.ring)
        if not CANOPY_AREA_M2[0] <= area <= CANOPY_AREA_M2[1]:
            continue
        _, _, hl, hw = _obox(b.ring)
        if area / max(4.0 * hl * hw, 1e-9) < CANOPY_RECT_MIN:
            continue
        v = c - sc
        for q, dist in fronts:
            axis = (q - sc) / dist
            t = float(v @ axis) / dist
            lateral = abs(float(v[0] * axis[1] - v[1] * axis[0]))
            if 0.0 < t <= 1.1 and lateral <= shl + 10.0:
                found.append(b)
                break
    return found[0] if len(found) == 1 else None


# --- the forecourt -----------------------------------------------------------------------------------------------
_WCA = "/levels/west_coast_usa/art/shapes/buildings/"
# West Coast USA's own canopy kits (docs/research/2026-10-23-gas-station-probe.md §1): the canopy shape, its model's
# half size across (x) and along (y), its pumps' positions in its frame (fuel_station_1/2/3/5), their yaw relative to
# the canopy, their height over its origin and their (base, brand) shapes (p_fuel_pump_<year>_<brand>.prefab.json)
KITS = (
    dict(shape=_WCA + "s_fuel_structure_01.dae", half=(8.11, 11.11), dyaw=0.0, dz=0.33,
         pumps=((-6.08, -6.07), (-2.76, 1.37), (-0.86, -8.40), (0.46, 8.60), (2.46, -0.95), (5.68, 6.28)),
         pump=(_WCA + "s_fuel_pump_2015.dae", _WCA + "s_fuel_pump_2015_trilobite.dae")),
    dict(shape=_WCA + "s_fuel_structure_02.dae", half=(7.30, 10.50), dyaw=0.0, dz=-0.03,
         pumps=((-7.69, -4.47), (-3.16, 2.18), (-1.46, -8.68), (1.31, 8.82), (3.00, -1.97), (7.54, 4.62)),
         pump=(_WCA + "s_fuel_pump_2000.dae", _WCA + "s_fuel_pump_2000_nodeoline.dae")),
    dict(shape=_WCA + "s_fuel_structure_03.dae", half=(6.50, 10.70), dyaw=180.0, dz=0.16,
         pumps=((-2.92, -7.43), (-2.92, 0.03), (-2.92, 7.56), (2.87, -7.44), (2.87, 0.08), (2.87, 7.56)),
         pump=(_WCA + "s_fuel_pump_2015.dae", _WCA + "s_fuel_pump_2015_apex.dae")),
    dict(shape=_WCA + "s_fuel_structure_05.dae", half=(6.61, 8.61), dyaw=0.0, dz=0.05,
         pumps=((-4.74, 2.38), (0.05, -5.30), (0.29, 5.50), (5.08, -2.16)),
         pump=(_WCA + "s_fuel_pump_2000.dae", _WCA + "s_fuel_pump_2000_tyranos.dae")),
)
KIT_SCALE = (0.7, 1.4)          # a kit's canopy scaled to its share of the mapped canopy, within this
BOLLARD = "/art/shapes/objects/bollard_yellow.dae"      # (global art; West Coast's fs_6: scale 1.5 1.5 1)
PAIR_GAP_M = 5.6                # the two pumps of a station without a canopy (fs_6)
# A refuel area per pump, centred on it (gasStationMarker.lua: the vehicle's box against the area's, its scale as
# half extents): 1.6 m across the pump row, 2.2 along it, 2.75 up (West Coast: 0.6 across, so a car must touch the
# pump; provisional until the in-game probe, probes/fuel_pump.lua)
AREA_HALF = (1.6, 2.2, 2.75)
AREA_DZ = 0.8
ICON_DZ = 2.5
ROAD_CLEAR_M = 3.0              # every piece of a pump pair this far from a public road's carriageway ...
BLD_CLEAR_M = 1.0               # ... and from every building
PAIR_HALF = (5.0, 4.0)          # the pump pair's footprint (half along the row, half across: the lanes beside it)


@dataclass
class Site:
    station: Station
    props: list         # {"shape", "x", "y", "dz", "yaw", "scale", "collide"} (anchor frame m, yaw degrees)
    areas: list         # {"x", "y", "dz", "yaw", "half", "icon"}: one refuel area per pump (icon: its height over it)
    drop_ids: tuple     # buildings left out of the mesh (the canopy outline)


def _h(*vals):
    return int.from_bytes(hashlib.blake2b(repr(vals).encode(), digest_size=8).digest(), "little")


def _yaw(u):
    return float(np.degrees(np.arctan2(u[1], u[0])))


def _pump(props, areas, kit, x, y, yaw, dz):
    py = (yaw + kit["dyaw"]) % 360.0
    props.append(dict(shape=kit["pump"][0], x=x, y=y, dz=dz, yaw=py, scale=[1.0, 1.0, 1.0], collide=True))
    props.append(dict(shape=kit["pump"][1], x=x, y=y, dz=dz, yaw=py, scale=[1.0, 1.0, 1.0], collide=False))
    areas.append(dict(x=x, y=y, dz=dz + AREA_DZ, yaw=yaw % 360.0, half=list(AREA_HALF), icon=ICON_DZ))


def _canopy_site(st, canopy, kit):
    c, u, hl, hw = _obox(canopy.ring)
    v = np.array([-u[1], u[0]])                 # the kit's x (across); its y (along) is u
    kx, ky = kit["half"]
    n = max(1, int(round(hl / ky)))
    sy = float(np.clip(hl / n / ky, *KIT_SCALE))
    sx = float(np.clip(hw / kx, *KIT_SCALE))
    yaw = _yaw(u) - 90.0                        # the model's +y onto u
    props, areas = [], []
    for k in range(n):
        o = c + u * ((2 * k + 1 - n) * hl / n)
        props.append(dict(shape=kit["shape"], x=float(o[0]), y=float(o[1]), dz=0.0, yaw=yaw % 360.0,
                          scale=[sx, sy, 1.0], collide=True))
        for lx, ly in kit["pumps"]:
            p = o + v * (lx * sx) + u * (ly * sy)
            _pump(props, areas, kit, float(p[0]), float(p[1]), yaw, kit["dz"])
    return Site(st, props, areas, (canopy.id,))


def _seg_dist(pts, a, b):
    """(M,) distance of each point to the nearest of segments a -> b ((K, 2) each)."""
    d = b - a
    L2 = np.maximum((d * d).sum(1), 1e-12)
    t = np.clip(((pts[:, None, :] - a[None]) * d[None]).sum(2) / L2[None], 0.0, 1.0)
    q = a[None] + d[None] * t[:, :, None]
    return np.hypot(*(q - pts[:, None, :]).transpose(2, 0, 1)).min(1)


def _clear(pts, blds, roads):
    """Whether every point is ROAD_CLEAR_M off every public road's carriageway and BLD_CLEAR_M off every building."""
    import polygon
    for line, half in roads:
        if (_seg_dist(pts, line[:-1], line[1:]) < half + ROAD_CLEAR_M).any():
            return False
    for b in blds:
        lo, hi = b.ring.min(0) - BLD_CLEAR_M, b.ring.max(0) + BLD_CLEAR_M
        near = pts[((pts >= lo) & (pts <= hi)).all(1)]
        if not len(near):
            continue
        if polygon.contains_points(b.ring, near).any() or (_seg_dist(near, b.ring[:-1], b.ring[1:]) < BLD_CLEAR_M).any():
            return False
    return True


def _pair_site(st, shop, blds, roads):
    """Two pumps in a row parallel to the shop's front, on open ground between the shop and its nearest road, as near
    the shop as fits; None when nothing fits."""
    sc = _centre(shop.ring)
    q, dist, _ = _nearest_on(roads, sc)
    if q is None or dist < 1.0:
        return None
    a = (q - sc) / dist                         # toward the road
    r = np.array([-a[1], a[0]])                 # along the row
    _, su, shl, shw = _obox(shop.ring)
    reach = float(abs(su @ a) * shl + abs(su[0] * a[1] - su[1] * a[0]) * shw)    # the shop's half depth toward the road
    kit = KITS[_h(st.id, "kit") % len(KITS)]
    gr, ga = np.meshgrid(np.linspace(-PAIR_HALF[0], PAIR_HALF[0], 11), np.linspace(-PAIR_HALF[1], PAIR_HALF[1], 9))
    grid = np.c_[gr.ravel(), ga.ravel()]
    for t in np.arange(reach + PAIR_HALF[1] + BLD_CLEAR_M, dist, 1.0):
        for lat in (0.0, 4.0, -4.0, 8.0, -8.0):
            c = sc + a * t + r * lat
            if not _clear(c + grid[:, :1] * r + grid[:, 1:] * a, blds, roads):
                continue
            props, areas = [], []
            yaw = _yaw(r) - 90.0                # the kit's y (its pump rows) along r
            for s in (-0.5, 0.5):
                p = c + r * (s * PAIR_GAP_M)
                _pump(props, areas, kit, float(p[0]), float(p[1]), yaw, 0.0)
            for s in (-1.0, 1.0):
                for o in (-0.3, 0.3):
                    p = c + r * (s * (PAIR_GAP_M / 2 + 1.2)) + a * o
                    props.append(dict(shape=BOLLARD, x=float(p[0]), y=float(p[1]), dz=0.0, yaw=_yaw(r) % 360.0,
                                      scale=[1.5, 1.5, 1.0], collide=True))
            return Site(st, props, areas, ())
    return None


def layout(st, blds, roads):
    """The station's forecourt (a Site), or None: the shop (find_shop); under its canopy (find_canopy) West Coast
    USA's canopy kit(s), hashed by station id, scaled to the canopy, the canopy outline dropped from the buildings;
    without one, a pump pair clear of roads and buildings. A convenience brand alone (not st.confirmed) needs the
    canopy."""
    shop = find_shop(st, blds)
    if shop is None:
        return None
    canopy = find_canopy(st, shop, blds, roads)
    if canopy is not None:
        return _canopy_site(st, canopy, KITS[_h(st.id, "kit") % len(KITS)])
    if not st.confirmed:
        return None
    return _pair_site(st, shop, blds, roads)


# --- the map data around a station, and the road tile's records --------------------------------------------------
def _outer(f, i, xy):
    a = f.coord_off[f.ring_off[f.part_off[i]]]
    b = f.coord_off[f.ring_off[f.part_off[i]] + 1]
    return xy[a:b]


def buildings_near(frame, layers, x, y, r):
    """Bld of the buildings (mapgeom.Features, one per map cell; a building in two cells taken once, by id) whose
    outline's centre is within r of (x, y) in frame."""
    out, seen = [], set()
    for f in layers:
        if f is None or len(f) == 0:
            continue
        a = f.attrs
        xy = f.xy_in(frame)
        for i in range(len(f)):
            fid = str(f.ids[i])
            if fid in seen or f.part_off[i + 1] == f.part_off[i]:
                continue
            ring = np.asarray(_outer(f, i, xy), np.float64)
            if len(ring) < 4 or np.hypot(*(ring[:-1].mean(0) - (x, y))) > r:
                continue
            seen.add(fid)

            def txt(k):
                v = a[k][i] if k in a else None
                return "" if v is None else str(v)
            h = a["height"][i] if "height" in a else None
            out.append(Bld(fid, ring, txt("class"), txt("name"), txt("src_dataset"),
                           float(h) if h is not None and np.isfinite(h) else None))
    return out


def drive_lines(frame, layers, x, y, r):
    """(centreline (k, 2), half carriageway width) of the public roads (buildings.THROUGH_CLASSES) with a point within
    r of (x, y) in frame, each road once."""
    import buildings
    import roads
    out, seen = [], set()
    for f in layers:
        if f is None or len(f) == 0 or "class" not in f.attrs:
            continue
        xy = f.xy_in(frame)
        for i in range(len(f)):
            fid, cls = str(f.ids[i]), str(f.attrs["class"][i])
            if fid in seen or cls not in buildings.THROUGH_CLASSES or f.part_off[i + 1] == f.part_off[i]:
                continue
            line = np.asarray(_outer(f, i, xy), np.float64)
            if len(line) < 2 or _seg_dist(np.array([[x, y]]), line[:-1], line[1:])[0] > r:
                continue
            seen.add(fid)
            out.append((line, roads.carriageway(cls, "paved", False) / 2.0))
    return out


REACH_M = SHOP_SEARCH_M + CANOPY_SEARCH_M + 10.0    # a station's canopy is within this of its position


def _cells_near(frame, x, y, r):
    import mapcells
    la, lo, _ = frame.enu_to_geodetic(np.array([x - r, x + r, x - r, x + r]), np.array([y - r, y - r, y + r, y + r]),
                                      0.0)
    return mapcells.cells_for_bbox(float(np.min(la)), float(np.min(lo)), float(np.max(la)), float(np.max(lo)))


def _record(site, frame, elev):
    """A site as the road tile's JSON record (anchor frame, heights on the ground: elev(lat, lon))."""
    pts = [(p["x"], p["y"]) for p in site.props] + [(a["x"], a["y"]) for a in site.areas]
    xs, ys = np.array([p[0] for p in pts], np.float64), np.array([p[1] for p in pts], np.float64)
    la, lo, _ = frame.enu_to_geodetic(xs, ys, 0.0)
    z = np.asarray(frame.geodetic_to_enu(la, lo, np.asarray(elev(la, lo), np.float64))[2], np.float64).ravel()
    sla, slo, _ = frame.enu_to_geodetic(np.array([site.station.x]), np.array([site.station.y]), 0.0)
    n = len(site.props)

    def r3(v):
        return round(float(v), 3)
    return {"id": site.station.id, "name": site.station.name, "brand": site.station.brand,
            "ll": [round(float(sla[0]), 7), round(float(slo[0]), 7)],
            "props": [{"shape": p["shape"], "pos": [r3(p["x"]), r3(p["y"]), r3(z[k] + p["dz"])], "yaw": round(p["yaw"], 2),
                       "scale": [round(v, 3) for v in p["scale"]], "collide": p["collide"]}
                      for k, p in enumerate(site.props)],
            "areas": [{"pos": [r3(a["x"]), r3(a["y"]), r3(z[n + k] + a["dz"])], "yaw": round(a["yaw"], 2),
                       "half": list(a["half"]), "icon": [r3(a["x"]), r3(a["y"]), r3(z[n + k] + a["dz"] + a["icon"])]}
                      for k, a in enumerate(site.areas)]}


def tile_sites(frame, tx, ty, md, elev):
    """(records of the stations whose position lies in road tile (tx, ty), ids of the canopy outlines to leave out of
    its buildings): every station within REACH_M of the tile is laid out, so a canopy whose centroid (buildings.py's
    owner rule) is in this tile is dropped here even when its station is the neighbour's. md: mapdata.MapData;
    elev(lat, lon) the physics ground. Without places data (offline, no file): no stations."""
    import roads
    x0, y0, x1, y1 = roads.tile_bbox(tx, ty)
    try:
        keys = roads.cells_of_tile(frame, tx, ty, margin=REACH_M + MERGE_M)
        places = [md.cell(k, layers=("places",), kind="demand").layers.get("places") for k in keys]
        stations = merge(pins(frame, places))
    except Exception as e:      # an unreachable source costs the stations, not the tile
        log.debug("gas stations of road tile %d_%d: %s", tx, ty, e)
        return [], frozenset()
    recs, skip = [], set()
    for st in stations:
        if not (x0 - REACH_M <= st.x < x1 + REACH_M and y0 - REACH_M <= st.y < y1 + REACH_M):
            continue
        try:
            cds = [md.cell(k, layers=("buildings", "roads"), kind="demand")
                   for k in _cells_near(frame, st.x, st.y, REACH_M)]
            site = layout(st, buildings_near(frame, [c.layers.get("buildings") for c in cds], st.x, st.y, REACH_M),
                          drive_lines(frame, [c.layers.get("roads") for c in cds], st.x, st.y, REACH_M + 50.0))
        except Exception as e:
            log.debug("gas station %s: %s", st.id, e)
            continue
        if site is None:
            continue
        skip.update(site.drop_ids)
        if x0 <= st.x < x1 and y0 <= st.y < y1:
            recs.append(_record(site, frame, elev))
    return recs, frozenset(skip)


PARK_CLEAR_M = 3.0      # a parked car's spot centre this far from every refuel area's edge and every prop


def clear_park(park, recs):
    """parking.py's spot rows [x, y, z, heading, ...] without those on a station's forecourt (recs: tile_sites')."""
    pts = [(p["pos"][0], p["pos"][1], PARK_CLEAR_M) for r in recs for p in r["props"]]
    pts += [(a["pos"][0], a["pos"][1], max(a["half"][0], a["half"][1]) + PARK_CLEAR_M) for r in recs for a in r["areas"]]
    if not pts or not park:
        return park
    return [s for s in park if all((s[0] - x) ** 2 + (s[1] - y) ** 2 > d * d for x, y, d in pts)]
