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
import math
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
    # Sheetz: all 48 of its places around Pittsburgh are filed as gas stations, and one of its stores near Pittsburgh has
    # no canopy in any building data, so the convenience rule left it without pumps (the user, 2026-10-23: move it)
    "Q7492551": "Sheetz",
}
# Convenience brands with fuel at some of their stores: a match needs a canopy or a gas_station pin too.
CONV_BRANDS = {
    "Q5936320": "Wawa", "Q259340": "7-Eleven", "Q3268010": "Circle K",
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


# words that say nothing about which station a pin is ("Joe Gas Mart" and "Main Street Gas" are two stations)
GENERIC_WORDS = {"gas", "station", "stations", "fuel", "fuels", "petrol", "filling", "service", "services", "mart",
                 "market", "mini", "food", "foods", "shop", "store", "stores", "oil", "express", "the", "and", "stop",
                 "center", "centre", "travel", "plaza", "convenience", "quick", "inc", "llc", "company", "corp"}


def _tokens(name):
    return {t for t in re.findall(r"[a-z]+", name.lower()) if len(t) >= 3}


def _compatible(a, b):
    """Two pins can be one station: the same brand id, or a shared distinctive name word, or one of them without one
    (an unnamed pin)."""
    if a.wikidata and b.wikidata:
        return a.wikidata == b.wikidata
    ta = _tokens(a.name + " " + a.brand) - GENERIC_WORDS
    tb = _tokens(b.name + " " + b.brand) - GENERIC_WORDS
    if not ta or not tb:
        return True
    return bool(ta & tb)


def _display_name(name, brand):
    """The name the refuel prompt shows: the place's name, or its brand when the name does not mention it (Overture
    names some stations after their operator: the Munich Aral's "Wolfgang Tulach", 2026-10-23)."""
    for known in (*FUEL_BRANDS.values(), *CONV_BRANDS.values()):     # ("Buc-ee's Katy Texas" in New Braunfels)
        if brand.lower().startswith(known.lower() + " "):
            brand = known
            break
    if not brand or not name:
        return name or brand
    if brand.lower() in name.lower() or (_tokens(brand) - GENERIC_WORDS) & _tokens(name):
        return name
    return brand


def merge(ps, merge_m=MERGE_M):
    """Stations of pins ps: pins within merge_m joined, nearest pairs first, while every pin of the one group is
    compatible with every pin of the other (an unbranded pin between a Shell and a Sunoco across a street joins one of
    them, not both: review I2)."""
    ps = sorted(ps, key=lambda p: p.id)
    pairs = sorted(((a.x - b.x) ** 2 + (a.y - b.y) ** 2, i, j) for i, a in enumerate(ps) for j, b in enumerate(ps)
                   if i < j and (a.x - b.x) ** 2 + (a.y - b.y) ** 2 <= merge_m ** 2)
    group = {i: [i] for i in range(len(ps))}
    of = list(range(len(ps)))
    for _, i, j in pairs:
        gi, gj = of[i], of[j]
        if gi == gj or not all(_compatible(ps[p], ps[q]) for p in group[gi] for q in group[gj]):
            continue
        for q in group[gj]:
            of[q] = gi
        group[gi] += group.pop(gj)
    out = []
    for g in ([ps[i] for i in sorted(m)] for m in group.values()):
        ids = sorted(p.id for p in g)
        named = max(g, key=lambda p: (bool(p.brand or p.wikidata), p.gas))     # (a branded pin, else a gas one)
        out.append(Station(hashlib.blake2b("|".join(ids).encode(), digest_size=6).hexdigest(),
                           float(np.mean([p.x for p in g])), float(np.mean([p.y for p in g])),
                           _display_name(named.name, FUEL_BRANDS.get(named.wikidata) or CONV_BRANDS.get(named.wikidata)
                                         or named.brand),       # (the list's name: Overture's can be another store's)
                           named.brand or FUEL_BRANDS.get(named.wikidata)
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
# never a station's shop: homes and public buildings (Munich, 2026-10-23: a Shell's and an Agip's kiosks are not mapped,
# the nearest buildings were a hospital and a block of flats)
NOT_SHOP_CLASSES = {"apartments", "house", "residential", "detached", "semidetached_house", "terrace", "bungalow",
                    "dormitory", "houseboat", "static_caravan", "hospital", "school", "university", "college",
                    "kindergarten", "church", "cathedral", "chapel", "mosque", "temple", "synagogue", "shrine",
                    "religious", "hotel", "civic", "government", "public", "fire_station", "train_station",
                    "transportation", "stadium", "sports_hall", "grandstand", "greenhouse", "barn", "farm",
                    "farm_auxiliary", "cowshed", "stable", "silo", "military", "bunker", "parking"}
PIN_ROAD_M = 60.0           # no shop: the pumps at the pin (a kiosk not mapped) when a road is this close to it
ROOF_CLASSES = {"roof"}     # OSM building=roof (the Speedway's canopy: outbuilding / roof)
CANOPY_GAP_M = 30.0         # a tagged roof this close to the shop's outline, or to another canopy's, is a canopy (Buc-ee's
                            # New Braunfels: a 155 x 55 m store, two 157 m canopies whose centres are 52 and 90 m from
                            # its centre; the user, 2026-10-23: "canopys are buildings and we put two pumps outside")
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
        if b.cls in ROOF_CLASSES or b.cls in NOT_SHOP_CLASSES:
            continue
        d = float(np.hypot(*(_centre(b.ring) - p)))
        if d <= SHOP_SEARCH_M:
            near.append((not _named(b, st), b.src in ML_SOURCES, d, b.id, b))
    return min(near, key=lambda r: r[:4])[4] if near else None


def _gap(a, b):
    """The distance between two outlines (0 when they cross or one holds the other)."""
    import polygon
    if polygon.contains_points(a, b[:1]).any() or polygon.contains_points(b, a[:1]).any():
        return 0.0
    return float(min(_seg_dist(a, b[:-1], b[1:]).min(), _seg_dist(b, a[:-1], a[1:]).min()))


# a tagged roof is a canopy only in a canopy's shape (the real ones, 2026-10-23: Buc-ee's 157 x 21 m and 8.4 m high,
# the Speedway's 73 x 9 m, Munich's 118-328 m2; final review I1: not a market hall, a platform roof or a carport)
ROOF_AREA_MIN_M2 = 40.0
ROOF_SHORT_MAX_M = 25.0
ROOF_MAX_H_M = 10.0


def _canopy_shape(b):
    _, _, hl, hw = _obox(b.ring)
    a = _area(b.ring)
    return (a >= ROOF_AREA_MIN_M2 and 2 * min(hl, hw) <= ROOF_SHORT_MAX_M and a >= CANOPY_RECT_MIN * 4 * hl * hw
            and (b.height is None or b.height <= ROOF_MAX_H_M))


def find_canopies(st, shop, blds, roads):
    """The station's canopies: every tagged roof in a canopy's shape within CANOPY_GAP_M of the shop's outline or of a
    canopy found so far (a big station's rows of them); without any, find_canopy's one untagged imagery outline, if
    any."""
    roofs = [b for b in blds if b is not shop and b.cls in ROOF_CLASSES and _canopy_shape(b)]
    found, near = [], [shop]
    grew = True
    while grew:
        grew = False
        for b in roofs:
            if b not in found and any(_gap(b.ring, x.ring) <= CANOPY_GAP_M for x in near):
                found.append(b)
                near.append(b)
                grew = True
    if found:
        return sorted(found, key=lambda b: b.id)
    one = find_canopy(st, shop, blds, roads)
    return [one] if one is not None else []


def find_canopy(st, shop, blds, roads):
    """The station's canopy among blds, or None (then no building is touched): the nearest tagged roof within
    CANOPY_GAP_M of the shop's outline; else an untagged imagery outline (ML_SOURCES) that is not the shop, a canopy-sized
    rectangle no taller than CANOPY_MAX_H_M, between the shop and a public road (projected on the line from the
    shop's centre to that road's nearest point; the nearest road or one at most ROAD_SLACK_M farther), and the only one
    that passes (the user, 2026-10-23, on turning a car wash into a
    canopy: "I wouldn't like that")."""
    sc = _centre(shop.ring)
    roofs = [(_gap(b.ring, shop.ring), b.id, b) for b in blds if b is not shop and b.cls in ROOF_CLASSES]
    roofs = [r for r in roofs if r[0] <= CANOPY_GAP_M]
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
# West Coast USA's own canopy kits (docs/research/2026-10-23-gas-station-probe.md section 1): the canopy shape, its
# model's half size across (x) and along (y), its pumps' positions in its frame (fuel_station_1/2/3/5), their yaw
# relative to the canopy, their height over its origin and their (base, brand) shapes
# (p_fuel_pump_<year>_<brand>.prefab.json). Only the pump shapes are used now (a station's style, hashed): canopies are
# the roof slab over islands (_lane_site; the user, 2026-10-23, after a Sheetz near Pittsburgh: the same style for the
# Speedway's mapped canopy)
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
# every station's pumps: Trilobite's, as the roof slab's sign (the user, 2026-10-23: "the pumps are changing brands but
# the canopy is not"; West Coast has no other stretchable roof)
PUMP_KIT = KITS[0]
BOLLARD = "/art/shapes/objects/bollard_yellow.dae"      # (global art; West Coast's fs_6: scale 1.5 1.5 1)
PAIR_GAP_M = 5.6                # the two pumps of a station without a canopy (fs_6)
# A refuel area per pump, centred on it (gasStationMarker.lua: the vehicle's box against the area's, its scale as
# half extents), in the pump's frame: a pump model's x runs along its island and its screens face +-y, the cars either
# side (West Coast's fuel_station_4: pumps at yaw 0 in rows along x, areas 0.6 along x, 2.2 across): 1.6 m along the
# island, 2.2 across it, 2.75 up (provisional until the in-game probe, probes/fuel_pump.lua)
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
    shop_id: str = ""   # one station per shop (unmerged pins of one station: tile_sites)


def _yaw(u):
    return float(np.degrees(np.arctan2(u[1], u[0])))


def _pump(props, areas, kit, x, y, yaw, dz, shapes=None):
    py = (yaw + kit["dyaw"]) % 360.0
    for k, shape in enumerate(shapes or kit["pump"]):     # (a base and its brand overlay: the base collides)
        props.append(dict(shape=shape, x=x, y=y, dz=dz, yaw=py, scale=[1.0, 1.0, 1.0], collide=k == 0))
    areas.append(dict(x=x, y=y, dz=dz + AREA_DZ, yaw=yaw % 360.0, half=list(AREA_HALF), icon=ICON_DZ))


def _area_pts(x, y, yaw):
    """The corners and centre of a refuel area (AREA_HALF along / across its island) at (x, y), its yaw in degrees."""
    a = np.radians(yaw)
    ex, ey = np.array([np.cos(a), np.sin(a)]), np.array([-np.sin(a), np.cos(a)])
    return np.array([(x, y)] + [(x, y) + ex * (sx * AREA_HALF[0]) + ey * (sy * AREA_HALF[1])
                                for sx in (-1, 1) for sy in (-1, 1)], np.float64)


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


PIN_NEAR_M = 20.0           # a pair beside the shop: this close to the pin


def _pair_site(st, shop, blds, roads, kit=None, at_pin=False):
    """Two pumps in a row parallel to the shop's front, on open ground between the shop and its nearest road, as near
    the shop as fits; without a shop, from the pin toward its nearest road; at_pin: from the pin, within PIN_NEAR_M of
    it and not behind the shop's back wall (a forecourt beside the shop); None when nothing fits."""
    pin = np.array([st.x, st.y])
    if at_pin and shop is not None:
        c0 = _centre(shop.ring)
        q0, d0, _ = _nearest_on(roads, c0)
        if q0 is None or d0 < 1.0:
            return None
        front = (q0 - c0) / d0                  # the shop's front: toward its road
        _, su, shl, shw = _obox(shop.ring)
        back = -float(abs(su @ front) * shl + abs(su[0] * front[1] - su[1] * front[0]) * shw)   # its back wall
        site = _pair_site(st, None, blds, roads, kit)
        if site is None:
            return None
        for p in site.props:
            if np.hypot(p["x"] - st.x, p["y"] - st.y) > PIN_NEAR_M or float((np.array([p["x"], p["y"]]) - c0) @ front) < back:
                return None
        return site
    sc = _centre(shop.ring) if shop is not None else pin
    q, dist, _ = _nearest_on(roads, sc)
    if q is None or dist < 1.0:
        return None
    a = (q - sc) / dist                         # toward the road
    r = np.array([-a[1], a[0]])                 # along the row
    if shop is not None:
        _, su, shl, shw = _obox(shop.ring)
        reach = float(abs(su @ a) * shl + abs(su[0] * a[1] - su[1] * a[0]) * shw)    # its half depth toward the road
    else:
        reach = -(PAIR_HALF[1] + BLD_CLEAR_M)   # (from the pin itself)
    kit = kit or PUMP_KIT
    gr, ga = np.meshgrid(np.linspace(-PAIR_HALF[0], PAIR_HALF[0], 11), np.linspace(-PAIR_HALF[1], PAIR_HALF[1], 9))
    grid = np.c_[gr.ravel(), ga.ravel()]
    steps = np.arange(reach + PAIR_HALF[1] + BLD_CLEAR_M, dist, 1.0)
    if shop is None:                            # from the pin both ways, nearest first (the forecourt may be behind it)
        steps = sorted(np.arange(-30.0, dist, 1.0), key=abs)
    for t in steps:
        for lat in (0.0, 4.0, -4.0, 8.0, -8.0):
            c = sc + a * t + r * lat
            if not _clear(c + grid[:, :1] * r + grid[:, 1:] * a, blds, roads):
                continue
            props, areas = [], []
            yaw = _yaw(r)                       # a pump's x along its row (fs_4)
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


# --- stations by region (the user, 2026-10-23: "I want to use them based on region so we should take advantage of the
# italy assets for europe") ---------------------------------------------------------------------------------------
EUROPE = {"alpine", "europe_north", "nordic", "mediterranean"}    # buildings.region_style's names
US_EAST = {"us_east", "us_south"}       # East Coast USA's units (the user, 2026-10-23: "check the east coast etc.")


def style_of(lat, lon):
    """"europe" (Italy's petrol station set), "us_east" (East Coast USA's) or "default" (West Coast USA's) for a
    station at (lat, lon)."""
    import buildings
    name = buildings.region_style(lat, lon).name
    return "europe" if name in EUROPE else "us_east" if name in US_EAST else "default"


_ITA = "/levels/italy/art/shapes/buildings/italy_petrolstation_"
# Italy's set, measured in game (2026-10-23): an awning half 12 m along its x, 6.53 m to its -y from its origin (two
# turned 180 degrees about one origin make a 12 x 13 m section, italy_big_petrolstation.prefab), its pillars built in;
# under each half a row of 2 pumps at x +-2.25, y -3.2 (the prefab's), turned as the awning (a pump's x along its row)
ITA_AWNING = _ITA + "awningSt.DAE"
ITA_HALF = (6.0, 6.53)
ITA_PUMP_XY = (2.25, 3.2)
ITA_PUMPS = (_ITA + "pump_b.DAE", _ITA + "pump_c.DAE")
ITA_SIGN = _ITA + "brandsign.DAE"
ITA_KIT = dict(pump=(ITA_PUMPS[0],), dyaw=0.0, dz=0.0)
ITA_SCALE = (0.6, 1.6)


def _italy_site(st, boxes, others, roads):
    """Italy's awning sections over each box (centre, long axis, half length, half width: a canopy's or the mapped
    lanes'), 12 m each along it and stretched across it, two pumps under each half where they are clear of roads and
    buildings, and the brand sign beside the end nearest a road; None without a pump."""
    props, areas = [], []
    for c, ul, hl, hw in boxes:
        v = np.array([-ul[1], ul[0]])
        yaw = _yaw(ul)
        k = max(1, int(round(hl / ITA_HALF[0])))
        sx = float(np.clip(hl / k / ITA_HALF[0], *ITA_SCALE))
        sy = float(np.clip(hw / ITA_HALF[1], *ITA_SCALE))
        for j in range(k):
            o = c + ul * ((2 * j + 1 - k) * hl / k)
            for side in (-1.0, 1.0):                # (the -y half as modelled, then the one turned to +y)
                props.append(dict(shape=ITA_AWNING, x=float(o[0]), y=float(o[1]), dz=0.0,
                                  yaw=(yaw if side < 0 else yaw + 180.0) % 360.0, scale=[sx, sy, 1.0], collide=True))
                for i, ax in enumerate((-ITA_PUMP_XY[0], ITA_PUMP_XY[0])):
                    p = o + ul * (ax * sx) + v * (side * ITA_PUMP_XY[1] * sy)
                    if _clear(_area_pts(float(p[0]), float(p[1]), yaw), others, roads):
                        _pump(props, areas, ITA_KIT, float(p[0]), float(p[1]), yaw, 0.0,
                              shapes=(ITA_PUMPS[(j + i) % 2],))
        q, _, _ = _nearest_on(roads, c) if len(roads) else (None, 0.0, -1)
        end = ul if q is None or float((q - c) @ ul) >= 0 else -ul
        sign = c + end * (hl + 2.5)
        if _clear(np.array([sign]), others, roads):
            props.append(dict(shape=ITA_SIGN, x=float(sign[0]), y=float(sign[1]), dz=0.0, yaw=yaw % 360.0,
                              scale=[1.0, 1.0, 1.0], collide=True))
    if not areas:
        return None
    return Site(st, props, areas, ())


# East Coast USA's unit (eca_gastation_pumps.DAE; its collision mesh, 2026-10-23): one model of three islands, a
# dispenser on each and the roof over them, its origin at the ground. In its frame the islands are 1.46 m wide at
# x = -5.215, 1.24 and 7.74, run along y over -9.2 .. -4.53 (centre -6.865) and are 0.2 m high; the dispensers at
# y = -7.46; the roof 17.6 x 6.6 m (x -7.53 .. 10.1, y -10.04 .. -3.44) 4.7 m up. East Coast's own station (Gas_Station,
# fuel_node_city / fuel_node_dealer) has a refuel area per island at the dispenser, 4 m across the island.
ECA_UNIT = "/levels/east_coast_usa/art/shapes/buildings/eca_gastation_pumps.DAE"
ECA_ISLAND_X = (-5.215, 1.24, 7.74)
ECA_ISLAND_STEP = (ECA_ISLAND_X[2] - ECA_ISLAND_X[0]) / 2
ECA_ISLAND_Y = -6.865
ECA_PUMP_Y = -7.46
ECA_DEPTH = 6.6                 # the roof's depth (y): a row of units every this much along the islands
ECA_ROOF_XY = (1.285, -6.74)    # the roof's centre
ECA_SCALE = (0.8, 1.25)
ECA_LIFT_M = 0.05


def _eca_site(st, lanes, others, roads):
    """East Coast's units over lanes (mapped or made up under a canopy): an island between each two lanes, the units'
    islands on them (stretched to the lanes' gap; the last unit back over the one before when the islands are not a
    multiple of three), rows of units along the islands; a refuel area at each island's dispenser per row, where it is
    clear of roads and buildings. None without an area."""
    c, u = lanes.centre, lanes.u
    x = np.array([u[1], -u[0]])                 # the units' x: across the islands (their y along them, u)
    sign = 1.0 if float(x @ lanes.n) >= 0 else -1.0
    offs = lanes.offsets
    mids = [sign * (o0 + o1) / 2 for o0, o1 in zip(offs[:-1], offs[1:])]      # the islands along x
    if sign < 0:
        mids = mids[::-1]
    n_isl = len(mids)
    if n_isl < 3:                               # (a unit would overhang: final review I3)
        return None
    gap = (mids[-1] - mids[0]) / (n_isl - 1) if n_isl > 1 else ECA_ISLAND_STEP
    sx = float(np.clip(gap / ECA_ISLAND_STEP, *ECA_SCALE))
    length = lanes.a1 - lanes.a0
    rows = max(1, int(round(length / ECA_DEPTH)))
    sy = float(np.clip(length / rows / ECA_DEPTH, *ECA_SCALE))
    yaw = _yaw(x)
    props, areas, done = [], [], set()
    for row in range(rows):
        along = (lanes.a0 + lanes.a1) / 2 + (row - (rows - 1) / 2) * (length / rows)
        for j in range(max(1, int(math.ceil(n_isl / 3)))):
            first = max(0, min(3 * j, n_isl - 3))
            at = mids[first] + (ECA_ISLAND_X[1] - ECA_ISLAND_X[0]) * sx   # the unit's middle island
            o = c + x * (at - ECA_ISLAND_X[1] * sx) + u * (along - ECA_ISLAND_Y * sy)
            made = False
            for i, ix in enumerate(ECA_ISLAND_X):
                k = first + i
                if k >= n_isl or (row, k) in done:
                    continue
                done.add((row, k))
                p = c + x * mids[k] + u * (along - (ECA_ISLAND_Y - ECA_PUMP_Y) * sy)    # (the lanes' midpoint)
                if _clear(_area_pts(float(p[0]), float(p[1]), _yaw(u)), others, roads):
                    areas.append(dict(x=float(p[0]), y=float(p[1]), dz=AREA_DZ, yaw=_yaw(u) % 360.0,
                                      half=list(AREA_HALF), icon=ICON_DZ))
                    made = True
            if made:                            # (a unit back over the one before: its roof above, not in its plane)
                props.append(dict(shape=ECA_UNIT, x=float(o[0]), y=float(o[1]), dz=ECA_LIFT_M if first != 3 * j else 0.0,
                                  yaw=yaw % 360.0,
                                  scale=[round(sx, 3), round(sy, 3), 1.0], collide=True))
    if not areas:
        return None
    return Site(st, props, areas, ())


def _lanes_box(lanes):
    """(centre, long axis, half length, half width) of the mapped lanes' forecourt."""
    along = (lanes.a1 - lanes.a0) / 2
    across = (lanes.offsets[-1] - lanes.offsets[0]) / 2
    if along >= across:
        return lanes.centre, lanes.u, along, across
    return lanes.centre, lanes.n, across, along


# --- the forecourt's mapped drive lanes ----------------------------------------------------------------------------
# OpenStreetMap often maps a forecourt's drive lanes as short parallel service roads (a Sheetz near Pittsburgh:
# six of ~20 m, 7 m apart; the user, 2026-10-23: "you see the rows thats the roads for the pumps"). The pump islands
# stand between them: the most exact layout there is, and evidence of fuel for a convenience brand.
LANE_LEN_M = (10.0, 35.0)       # a lane's length
LANE_STRAIGHT = 0.95            # chord / length
LANE_ANGLE_DEG = 10.0           # lanes this parallel
LANE_GAP_M = (5.0, 10.0)        # neighbouring lanes this far apart (an island and a car either side)
LANE_SEARCH_M = 60.0            # lanes' midpoints this close to the shop's centre
LANE_MIN = 3                    # lanes of one forecourt (two islands)
ISLAND_END_M = 1.0              # an island stops this short of its lanes' ends
PUMP_STEP_M = 6.5               # pumps along an island (West Coast's fuel_station_4)
ISLAND_MAX_M = 20.0             # a longer island is split, a pillar at each piece's ends
SHORT_ISLAND_M = 10.0           # an island this short (a narrow canopy's, across it) has one dispenser in its middle, a
                                # pillar on it behind the pump and a bollard at each end (the user's photo, 2026-10-23)
ISLAND_BREAK_M = 2.0            # the gap between the pieces
_PILLAR = _WCA + "s_fuel_structure_07.dae"     # (fs_4: a 4.9 m pillar at each island's end ...)
_ROOF = _WCA + "s_fuel_structure_06.dae"       # (... and a 14 x 17.2 m roof slab 4.8 m up)
ROOF_HALF = (7.0, 8.61)
ROOF_DZ = 4.8


@dataclass
class Lanes:
    centre: np.ndarray      # (2,) the forecourt's centre
    u: np.ndarray           # along the lanes
    n: np.ndarray           # across them
    offsets: tuple          # each lane's offset along n from the centre, ascending
    a0: float               # the lanes' common span along u from the centre
    a1: float


def find_lanes(shop, lines):
    """The forecourt's drive lanes among service road polylines (anchor frame) near the shop, or None: the longest
    run of at least LANE_MIN short, straight, parallel ones LANE_GAP_M apart, overlapping along their length."""
    sc = _centre(shop.ring)
    cos_max = np.cos(np.radians(LANE_ANGLE_DEG))
    segs = []
    for line in lines:
        line = np.asarray(line, np.float64)[:, :2]
        if len(line) < 2:
            continue
        length = float(np.hypot(*np.diff(line, axis=0).T).sum())
        d = line[-1] - line[0]
        chord = float(np.hypot(*d))
        if not LANE_LEN_M[0] <= length <= LANE_LEN_M[1] or chord < LANE_STRAIGHT * length:
            continue
        mid = (line[0] + line[-1]) / 2
        if float(np.hypot(*(mid - sc))) > LANE_SEARCH_M:
            continue
        u = d / chord
        if u[0] < 0 or (u[0] == 0 and u[1] < 0):
            u = -u
        segs.append((mid, u, line[0], line[-1]))
    segs.sort(key=lambda g: (round(float(g[0][0]), 3), round(float(g[0][1]), 3)))
    best = None
    for mid, u, _, _ in segs:
        n = np.array([-u[1], u[0]])
        rows = []
        for m2, u2, p0, p1 in segs:
            if abs(float(u2 @ u)) < cos_max:
                continue
            a = sorted((float((p0 - mid) @ u), float((p1 - mid) @ u)))
            rows.append((float((m2 - mid) @ n), a[0], a[1]))
        seed = next(r for r in rows if abs(r[0]) < 1e-9)
        rows = [r for r in rows if min(r[2], seed[2]) - max(r[1], seed[1])
                >= 0.5 * min(r[2] - r[1], seed[2] - seed[1])]
        rows.sort()
        k = rows.index(seed)
        chain = [rows[k]]
        for step in (1, -1):
            cur, j = rows[k], k + step
            while 0 <= j < len(rows):
                gap = abs(rows[j][0] - cur[0])
                if gap > LANE_GAP_M[1]:
                    break
                if gap >= LANE_GAP_M[0]:
                    chain.append(rows[j])
                    cur = rows[j]
                j += step
        if len(chain) >= LANE_MIN and (best is None or len(chain) > len(best[2])):
            best = (mid, u, chain)
    if best is None:
        return None
    mid, u, chain = best
    n = np.array([-u[1], u[0]])
    offs = sorted(r[0] for r in chain)
    a0, a1 = float(np.median([r[1] for r in chain])), float(np.median([r[2] for r in chain]))
    o_mid, a_mid = (offs[0] + offs[-1]) / 2, (a0 + a1) / 2
    return Lanes(mid + n * o_mid + u * a_mid, u, n, tuple(o - o_mid for o in offs), a0 - a_mid, a1 - a_mid)


def _lane_site(st, lanes, others, roads, kit):
    """Islands between the lanes, a pump every PUMP_STEP_M along each (clear of roads and buildings, as every pump),
    a pillar at each island's end and the roof slab over all the lanes."""
    c, u, n = lanes.centre, lanes.u, lanes.n
    yaw = _yaw(u)                               # pumps and pillars: their x along the island (fs_4; the user,
    roof_yaw = yaw - 90.0                       # 2026-10-23: "pumps are facing the wrong way"); the roof slab's y
    length = (lanes.a1 - lanes.a0) - 2 * ISLAND_END_M
    if length < 3.0:
        return None
    pieces = max(1, int(math.ceil(length / ISLAND_MAX_M)))
    piece = (length - (pieces - 1) * ISLAND_BREAK_M) / pieces
    count = max(1, int(round(piece / PUMP_STEP_M)))
    step = min(PUMP_STEP_M, piece / count)
    props, areas = [], []
    if length <= SHORT_ISLAND_M:
        q, _, _ = _nearest_on(roads, c) if len(roads) else (None, 0.0, -1)
        back = -u if q is not None and float((q - c) @ u) > 0 else u          # away from the road
        for o0, o1 in zip(lanes.offsets[:-1], lanes.offsets[1:]):
            m = c + n * ((o0 + o1) / 2) + u * ((lanes.a0 + lanes.a1) / 2)
            if _clear(_area_pts(float(m[0]), float(m[1]), yaw), others, roads):
                _pump(props, areas, kit, float(m[0]), float(m[1]), yaw, 0.0)
            q = m + back * min(2.0, length / 2 - 0.6)
            props.append(dict(shape=_PILLAR, x=float(q[0]), y=float(q[1]), dz=0.0, yaw=yaw % 360.0,
                              scale=[1.0, 1.0, 1.0], collide=True))
            for sgn in (-1.0, 1.0):
                b = m + u * (sgn * (length / 2 + 0.3))
                props.append(dict(shape=BOLLARD, x=float(b[0]), y=float(b[1]), dz=0.0, yaw=yaw % 360.0,
                                  scale=[1.5, 1.5, 1.0], collide=True))
        pieces = 0
    for o0, o1 in zip(lanes.offsets[:-1], lanes.offsets[1:]):
        for j in range(pieces):
            m = c + n * ((o0 + o1) / 2) + u * (-length / 2 + piece / 2 + j * (piece + ISLAND_BREAK_M) + (lanes.a0 + lanes.a1) / 2)
            for k in range(count):
                p = m + u * ((k - (count - 1) / 2) * step)
                if _clear(_area_pts(float(p[0]), float(p[1]), yaw), others, roads):
                    _pump(props, areas, kit, float(p[0]), float(p[1]), yaw, 0.0)
            for s in (-1.0, 1.0):
                q = m + u * (s * (piece / 2 + 0.6))
                props.append(dict(shape=_PILLAR, x=float(q[0]), y=float(q[1]), dz=0.0, yaw=yaw % 360.0,
                                  scale=[1.0, 1.0, 1.0], collide=True))
    if not areas:
        return None
    across = (lanes.offsets[-1] - lanes.offsets[0]) + 2.0
    along = (lanes.a1 - lanes.a0) + 2.0
    props.append(dict(shape=_ROOF, x=float(c[0]), y=float(c[1]), dz=ROOF_DZ, yaw=roof_yaw % 360.0,
                      scale=[round(across / (2 * ROOF_HALF[0]), 3), round(along / (2 * ROOF_HALF[1]), 3), 1.0],
                      collide=True))
    return Site(st, props, areas, ())


ISLAND_GAP_M = 7.0              # islands under a mapped canopy without mapped lanes: one every this much (a Sheetz's
                                # lanes: 7 m apart) along its long side, each across its short side (the
                                # user's photo of a long narrow canopy, 2026-10-23: short islands across it, one after
                                # another)


def _outline_lanes(canopy):
    """Lanes made up for a mapped canopy without mapped ones: across its short side (its islands too), evenly along
    its long side about ISLAND_GAP_M apart, inside its outline."""
    c, ul, hl, hw = _obox(canopy.ring)
    u = np.array([-ul[1], ul[0]])               # the lanes (and islands) across the short side
    n = ul
    islands = max(1, int(round((2 * hl - 1.0) / ISLAND_GAP_M)))
    step = (2 * hl - 1.0) / islands
    offsets = tuple(-(hl - 0.5) + k * step for k in range(islands + 1))
    return Lanes(c, u, n, offsets, -hw, hw)


LANES_UNDER_M = 5.0             # mapped lanes beside a canopy: their centre this near its outline's box


def _under(p, canopy):
    c, ul, hl, hw = _obox(canopy.ring)
    d = p - c
    return abs(float(d @ ul)) <= hl + LANES_UNDER_M and abs(float(d[0] * ul[1] - d[1] * ul[0])) <= hw + LANES_UNDER_M


def layout(st, blds, roads, service=(), style="default"):
    """The station's forecourt (a Site), or None. The shop (find_shop); then, most exact first: the forecourt's mapped
    drive lanes among the service roads (find_lanes), else lanes made up under its canopy (find_canopy,
    _outline_lanes): islands between them under a roof slab (_lane_site); else a pump pair clear of roads and
    buildings. A canopy outline found is dropped from the buildings either way. A convenience brand alone (not
    st.confirmed) needs the lanes or the canopy."""
    shop = find_shop(st, blds)
    if shop is None:                            # (a kiosk not mapped: the pin is on the forecourt)
        q, dist, _ = _nearest_on(roads, np.array([st.x, st.y])) if len(roads) else (None, np.inf, -1)
        if not st.confirmed or q is None or dist > PIN_ROAD_M:
            return None
        site = _pair_site(st, None, blds, roads, ITA_KIT if style == "europe" else PUMP_KIT)
        if site is not None:
            site.shop_id = "pin:" + st.id
        return site
    canopies = find_canopies(st, shop, blds, roads)
    others = [b for b in blds if b not in canopies]
    drop = tuple(b.id for b in canopies)
    kit = PUMP_KIT
    site = None
    lanes = find_lanes(shop, service) if len(service) else None
    if lanes is not None and canopies and not any(_under(lanes.centre, c) for c in canopies):
        lanes = None                            # (another lot's lanes: final review I2)
    if style == "europe":
        boxes = [_lanes_box(lanes)] if lanes is not None else [_obox(c.ring) for c in canopies]
        if boxes:
            site = _italy_site(st, boxes, others, roads)
        elif st.confirmed:
            site = _pair_site(st, shop, blds, roads, ITA_KIT) or _pair_site(st, shop, blds, roads, ITA_KIT, at_pin=True)
        if site is not None:
            site.shop_id, site.drop_ids = shop.id, drop
        return site
    if style == "us_east":
        lane_site = lambda ln: (_eca_site(st, ln, others, roads)        # noqa: E731
                                or _lane_site(st, ln, others, roads, kit))
    else:
        lane_site = lambda ln: _lane_site(st, ln, others, roads, kit)   # noqa: E731
    if lanes is not None:
        site = lane_site(lanes)
    if site is None and canopies:
        parts = [x for x in (lane_site(_outline_lanes(c)) for c in canopies) if x is not None]
        if parts:
            site = Site(st, [p for x in parts for p in x.props], [a for x in parts for a in x.areas], ())
    if site is not None:
        site.drop_ids = drop
    if site is None and st.confirmed:
        site = _pair_site(st, shop, blds, roads) or _pair_site(st, shop, blds, roads, at_pin=True)
    if site is not None:
        site.shop_id = shop.id
    return site


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
        cx, cy = f.outer_centres(frame)         # (vectorised: a city cell has tens of thousands, review I6)
        near = np.flatnonzero((cx - x) ** 2 + (cy - y) ** 2 <= (r + 1.0) ** 2)
        for i in near.tolist():
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
    r of (x, y) in frame, each road once; the width as the road mesh draws it (roads.road_specs: lane counts, width
    tags, one-way: a six-lane arterial is wider than its class, review I5)."""
    import buildings
    import roads
    out, seen = [], set()
    for f in layers:
        if f is None or len(f) == 0 or "class" not in f.attrs:
            continue
        xy = f.xy_in(frame)
        bb = f.boxes(frame)                     # (only the roads near: review I6)
        near = np.flatnonzero((bb[:, 0] <= x + r) & (bb[:, 2] >= x - r) & (bb[:, 1] <= y + r) & (bb[:, 3] >= y - r))
        specs = f.__dict__.get("_gas_specs")
        for i in near.tolist():
            fid, cls = str(f.ids[i]), str(f.attrs["class"][i])
            if fid in seen or cls not in buildings.THROUGH_CLASSES or f.part_off[i + 1] == f.part_off[i]:
                continue
            line = np.asarray(_outer(f, i, xy), np.float64)
            if len(line) < 2 or _seg_dist(np.array([[x, y]]), line[:-1], line[1:])[0] > r:
                continue
            seen.add(fid)
            if specs is None:
                specs = f.__dict__.setdefault("_gas_specs", roads.road_specs(f))
            sp = specs[i]
            out.append((line, (sp.width if sp is not None else roads.carriageway(cls, "paved", False)) / 2.0))
    return out


def service_lines(frame, layers, x, y, r):
    """Polylines (k, 2) of the service roads (class service: driveways, forecourt lanes, parking aisles) with a point
    within r of (x, y) in frame, each road once (find_lanes picks the forecourt's lanes among them)."""
    out, seen = [], set()
    for f in layers:
        if f is None or len(f) == 0 or "class" not in f.attrs:
            continue
        xy = f.xy_in(frame)
        bb = f.boxes(frame)
        near = np.flatnonzero((bb[:, 0] <= x + r) & (bb[:, 2] >= x - r) & (bb[:, 1] <= y + r) & (bb[:, 3] >= y - r))
        for i in near.tolist():
            fid = str(f.ids[i])
            if fid in seen or str(f.attrs["class"][i]) != "service" or f.part_off[i + 1] == f.part_off[i]:
                continue
            seen.add(fid)
            line = np.asarray(_outer(f, i, xy), np.float64)
            if len(line) >= 2:
                out.append(line)
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
    sites = []
    for st in stations:
        if not (x0 - REACH_M <= st.x < x1 + REACH_M and y0 - REACH_M <= st.y < y1 + REACH_M):
            continue
        try:
            cds = [md.cell(k, layers=("buildings", "roads"), kind="demand")
                   for k in _cells_near(frame, st.x, st.y, REACH_M)]
            rls = [c.layers.get("roads") for c in cds]
            la, lo, _ = frame.enu_to_geodetic(st.x, st.y, 0.0)
            site = layout(st, buildings_near(frame, [c.layers.get("buildings") for c in cds], st.x, st.y, REACH_M),
                          drive_lines(frame, rls, st.x, st.y, REACH_M + 50.0),
                          service_lines(frame, rls, st.x, st.y, REACH_M), style_of(float(la), float(lo)))
        except Exception as e:
            log.debug("gas station %s: %s", st.id, e)
            continue
        if site is not None:
            sites.append(site)
    recs, skip, shops = [], set(), set()
    for site in sorted(sites, key=lambda s: (not s.station.confirmed, s.station.id)):
        # one forecourt per shop and per canopy (the same in every tile: all are laid out)
        if site.shop_id in shops or skip.intersection(site.drop_ids):
            continue
        shops.add(site.shop_id)
        skip.update(site.drop_ids)
        st = site.station
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
