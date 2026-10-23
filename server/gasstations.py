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
