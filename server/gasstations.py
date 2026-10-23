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
import re
from dataclasses import dataclass

import numpy as np

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


def classify(category, taxonomy, wikidata, status):
    """(gas, fuel_brand, conv_brand) of a place, or None when it is no candidate (or closed)."""
    if status is not None and str(status) in CLOSED:
        return None
    gas = str(category or "") in GAS_CATEGORIES or str(taxonomy or "") in GAS_CATEGORIES
    wd = str(wikidata or "")
    fuel, conv = wd in FUEL_BRANDS, wd in CONV_BRANDS
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
                         col("operating_status", i) or None)
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
    station, else the nearest; None without one."""
    p = np.array([st.x, st.y])
    near = []
    for b in blds:
        if b.cls in ROOF_CLASSES:
            continue
        d = float(np.hypot(*(_centre(b.ring) - p)))
        if d <= SHOP_SEARCH_M:
            near.append((not _named(b, st), d, b.id, b))
    return min(near, key=lambda r: r[:3])[3] if near else None


def find_canopy(st, shop, blds, roads):
    """The station's canopy among blds, or None (then no building is touched): the nearest tagged roof within
    CANOPY_SEARCH_M of the shop; else an untagged imagery outline (ML_SOURCES) that is not the shop, a canopy-sized
    rectangle no taller than CANOPY_MAX_H_M, between the shop and its nearest road (projected on the line from the
    shop's centre to that road), and the only one that passes (the user, 2026-10-23, on turning a car wash into a
    canopy: "I wouldn't like that")."""
    sc = _centre(shop.ring)
    roofs = [(float(np.hypot(*(_centre(b.ring) - sc))), b.id, b) for b in blds
             if b is not shop and b.cls in ROOF_CLASSES]
    roofs = [r for r in roofs if r[0] <= CANOPY_SEARCH_M]
    if roofs:
        return min(roofs, key=lambda r: r[:2])[2]
    q, dist, _ = _nearest_on(roads, sc)
    if q is None or dist < 1.0:
        return None
    axis = (q - sc) / dist
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
        t = float(v @ axis) / dist
        lateral = abs(float(v[0] * axis[1] - v[1] * axis[0]))
        if 0.0 < t <= 1.1 and lateral <= shl + 10.0:
            found.append(b)
    return found[0] if len(found) == 1 else None
