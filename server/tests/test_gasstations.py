"""gasstations: which Overture places are fuel stations (categories, two brand lists), their pins merged into one
station, the shop and the canopy found among the buildings, and the forecourt laid out clear of roads and buildings
(the user, 2026-10-23: "the refueling is the main part")."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gasstations as gs  # noqa: E402


def pin(pid, x, y, name="", brand="", wikidata="", gas=False):
    c = gs.classify("gas_station" if gas else "convenience_store", "", wikidata, "open")
    g, fb, cb = c if c is not None else (gas, False, False)
    return gs.Pin(pid, x, y, name, brand, wikidata, g, fb, cb)


class ClassifyTest(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(gs.classify("gas_station", "gas_station", "", "open"), (True, False, False))
        self.assertEqual(gs.classify("fueling_station", "", "", None), (True, False, False))
        # a Sheetz near Pittsburgh: a convenience store by its category, fuel by its brand (a fuel brand:
        # the user, 2026-10-23; one of its stores has no canopy in any building data)
        self.assertEqual(gs.classify("convenience_store", "convenience_store", "Q7492551", "open"), (False, True, False))
        self.assertEqual(gs.classify("convenience_store", "", "Q5936320", "open"), (False, False, True))   # Wawa
        # "7-ELEVEN/SPEEDWAY #40001": Western Union by its category, no brand
        self.assertIsNone(gs.classify("financial_service", "money_transfer_service", "", "open"))
        self.assertEqual(gs.classify("atm", "atm", "Q458363", "open"), (False, True, False))     # Marathon
        self.assertIsNone(gs.classify("gas_station", "gas_station", "", "closed"))
        self.assertIsNone(gs.classify("gas_station", "gas_station", "", "permanently_closed"))
        self.assertEqual(gs.classify("gas_station", "gas_station", "Q458363", "open"), (True, True, False))

    def test_brand_name_without_wikidata(self):
        # a Sheetz near Pittsburgh carries the brand's name but no Wikidata id (found 2026-10-23)
        self.assertEqual(gs.classify("convenience_store", "convenience_store", "", "open", "Sheetz"), (False, True, False))
        self.assertEqual(gs.classify("atm", "atm", None, "open", "MARATHON"), (False, True, False))
        self.assertIsNone(gs.classify("convenience_store", "", "", "open", "Sheetz Bakery"))

    def test_brand_lists_disjoint(self):
        self.assertFalse(set(gs.FUEL_BRANDS) & set(gs.CONV_BRANDS))
        self.assertIn("Q7492551", gs.FUEL_BRANDS)       # Sheetz (all 48 around Pittsburgh filed as gas stations)
        self.assertIn("Q5936320", gs.CONV_BRANDS)       # Wawa
        self.assertIn("Q565734", gs.FUEL_BRANDS)        # Aral


class MergeTest(unittest.TestCase):
    def test_merge_speedway_pins(self):
        # A Speedway near Pittsburgh: DAC's pin on the shop, AllThePlaces' beside the pumps, 55 m apart
        a = pin("dac", 0.0, 0.0, "Speedway", gas=True)
        b = gs.Pin("atp", 40.0, 38.0, "7-ELEVEN/SPEEDWAY #40001", "", "", False, False, False)
        st = gs.merge([a, b])
        self.assertEqual(len(st), 1)
        self.assertTrue(st[0].confirmed)
        self.assertEqual(set(st[0].pins), {"dac", "atp"})
        self.assertEqual(st[0].name, "Speedway")

    def test_different_brands_close_not_merged(self):
        a = pin("shell", 0.0, 0.0, "Shell", "Shell", "Q110716465", gas=True)
        b = pin("sunoco", 40.0, 0.0, "Sunoco", "Sunoco", "Q1423218", gas=True)
        self.assertEqual(len(gs.merge([a, b])), 2)

    def test_same_brand_pins_merged(self):
        a = pin("m1", 0.0, 0.0, "Sunoco", "Sunoco", "Q1423218", gas=True)
        b = pin("m2", 30.0, 10.0, "Sunoco Gas Station", "", "", gas=True)
        self.assertEqual(len(gs.merge([a, b])), 1)

    def test_unbranded_pin_does_not_chain_brands(self):
        # review I2: an unbranded gas pin between a Shell and a Sunoco across the street joined both into one station
        a = pin("shell", 0.0, 0.0, "Shell", "Shell", "Q110716465", gas=True)
        m = pin("mid", 20.0, 0.0, "", gas=True)
        b = pin("sunoco", 40.0, 0.0, "Sunoco", "Sunoco", "Q1423218", gas=True)
        self.assertEqual(len(gs.merge([a, m, b])), 2)

    def test_generic_name_words_do_not_merge(self):
        a = pin("joe", 0.0, 0.0, "Joe Gas Mart", gas=True)
        b = pin("main", 40.0, 0.0, "Main Street Gas", gas=True)
        self.assertEqual(len(gs.merge([a, b])), 2)

    def test_far_pins_not_merged(self):
        a = pin("a", 0.0, 0.0, "Sunoco", "Sunoco", "Q1423218", gas=True)
        b = pin("b", 200.0, 0.0, "Sunoco", "Sunoco", "Q1423218", gas=True)
        self.assertEqual(len(gs.merge([a, b])), 2)

    def test_conv_brand_alone_not_confirmed(self):
        st = gs.merge([pin("w", 0.0, 0.0, "Wawa", "Wawa", "Q5936320")])
        self.assertFalse(st[0].confirmed)

    def test_station_id_stable(self):
        ps = [pin("dac", 0.0, 0.0, "Speedway", gas=True), pin("x", 300.0, 0.0, "Shell", "Shell", "Q110716465", gas=True),
              gs.Pin("atp", 40.0, 38.0, "7-ELEVEN/SPEEDWAY #40001", "", "", False, False, False)]
        self.assertEqual(sorted(s.id for s in gs.merge(ps)), sorted(s.id for s in gs.merge(list(reversed(ps)))))


def rect(bid, cx, cy, w, h, cls="", name="", src="OpenStreetMap", height=None):
    """A building: w along x, h along y, centred at (cx, cy)."""
    x0, x1, y0, y1 = cx - w / 2, cx + w / 2, cy - h / 2, cy + h / 2
    ring = np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)], np.float64)
    return gs.Bld(bid, ring, cls, name, src, height)


ROAD = [(np.array([(-300.0, 0.0), (300.0, 0.0)]), 5.0)]       # a street along y = 0, 10 m wide
STATION = gs.Station("st", 0.0, 45.0, "Speedway", "", ("p",), True)
SHOP = rect("shop", 0.0, 45.0, 20.0, 18.0, cls="retail")
ML = "Microsoft ML Buildings"


class CanopyTest(unittest.TestCase):
    def test_tagged_roof_is_canopy(self):
        roof = rect("roof", 0.0, 22.0, 30.0, 21.0, cls="roof")
        self.assertIs(gs.find_canopy(STATION, SHOP, [SHOP, roof], ROAD), roof)

    def test_single_ml_candidate_is_canopy(self):
        # a Sheetz near Pittsburgh: the shop named in OSM, the canopy an untagged ML outline 1.48 m high
        shop = rect("shop", 0.0, 45.0, 27.0, 20.3, name="Sheetz")
        can = rect("ml", 0.0, 20.0, 28.0, 18.0, src=ML, height=1.48)
        self.assertIs(gs.find_canopy(STATION, shop, [shop, can], ROAD), can)

    def test_two_ml_candidates_none(self):
        a = rect("ml1", -8.0, 20.0, 14.0, 14.0, src=ML)
        b = rect("ml2", 9.0, 21.0, 14.0, 14.0, src=ML)
        self.assertIsNone(gs.find_canopy(STATION, SHOP, [SHOP, a, b], ROAD))

    def test_ml_behind_shop_none(self):
        # a car wash behind the shop (the user, 2026-10-23: replacing one "I wouldn't like that")
        wash = rect("wash", 0.0, 70.0, 28.0, 12.0, src=ML)
        self.assertIsNone(gs.find_canopy(STATION, SHOP, [SHOP, wash], ROAD))

    def test_ml_toward_side_road_on_a_corner(self):
        # a Sheetz near Pittsburgh: the canopy toward the second-nearest road (59 m; the nearest 49 m the other way)
        side = (np.array([(-60.0, -300.0), (-60.0, 300.0)]), 5.0)          # 60 m west of the shop (front: 45 m)
        can = rect("ml", -30.0, 45.0, 18.0, 28.0, src=ML, height=1.48)
        self.assertIs(gs.find_canopy(STATION, SHOP, [SHOP, can], ROAD + [side]), can)

    def test_ml_toward_far_back_road_none(self):
        back = (np.array([(-300.0, 120.0), (300.0, 120.0)]), 5.0)          # 75 m behind the shop (front: 45 m)
        wash = rect("wash", 0.0, 70.0, 28.0, 12.0, src=ML)
        self.assertIsNone(gs.find_canopy(STATION, SHOP, [SHOP, wash], ROAD + [back]))

    def test_osm_untagged_not_canopy(self):
        b = rect("osm", 0.0, 20.0, 28.0, 18.0)
        self.assertIsNone(gs.find_canopy(STATION, SHOP, [SHOP, b], ROAD))

    def test_ml_wrong_shape_none(self):
        ring = np.array([(-14, 11), (14, 11), (14, 15), (-10, 15), (-10, 29), (-14, 29), (-14, 11)], np.float64)
        ell = gs.Bld("ell", ring, "", "", ML, None)
        self.assertIsNone(gs.find_canopy(STATION, SHOP, [SHOP, ell], ROAD))

    def test_ml_too_big_none(self):
        b = rect("big", 0.0, 22.0, 60.0, 20.0, src=ML)
        self.assertIsNone(gs.find_canopy(STATION, SHOP, [SHOP, b], ROAD))

    def test_shop_ignores_roof(self):
        roof = rect("roof", 0.0, 22.0, 30.0, 21.0, cls="roof")
        st = gs.Station("st", 0.0, 25.0, "Speedway", "", ("p",), True)     # the pin under the canopy
        self.assertIs(gs.find_shop(st, [roof, SHOP]), SHOP)

    def test_shop_prefers_name_match(self):
        other = rect("other", 0.0, 30.0, 10.0, 10.0, cls="retail")
        named = rect("named", 30.0, 45.0, 20.0, 18.0, name="Speedway")
        st = gs.Station("st", 0.0, 32.0, "Speedway", "", ("p",), True)
        self.assertIs(gs.find_shop(st, [other, named]), named)

    def test_shop_prefers_mapped_over_imagery(self):
        # a Sunoco near Pittsburgh: the pin on the road, the nearest outline an untagged imagery one (likely the
        # canopy), the store a mapper's building behind it
        ml = rect("ml", 17.0, 10.0, 13.0, 13.0, src=ML)
        osm = rect("osm", 39.0, 30.0, 14.0, 13.0)
        st = gs.Station("sun", 0.0, 0.0, "Sunoco", "", ("p",), True)
        self.assertIs(gs.find_shop(st, [ml, osm]), osm)
        self.assertIs(gs.find_shop(st, [ml]), ml)               # where imagery is all there is

    def test_no_shop_far(self):
        self.assertIsNone(gs.find_shop(STATION, [rect("far", 300.0, 300.0, 20.0, 20.0)]))


def inside_box(bld, x, y, pad=0.5):
    c, u, hl, hw = gs._obox(bld.ring)
    v = np.array([x, y]) - c
    return abs(v @ u) <= hl + pad and abs(v[0] * u[1] - v[1] * u[0]) <= hw + pad


def road_gap(x, y):
    return abs(y) - 5.0         # from ROAD's carriageway edge


class LayoutTest(unittest.TestCase):
    def test_canopy_layout(self):
        roof = rect("roof", 0.0, 22.0, 30.0, 21.0, cls="roof")
        site = gs.layout(STATION, [SHOP, roof], ROAD)
        self.assertEqual(site.drop_ids, ("roof",))
        canopies = [p for p in site.props if "s_fuel_structure_" in p["shape"]]
        self.assertEqual(len(canopies), 1)              # 15 m half length: one kit (half 8.6-11.1 m) along it
        self.assertGreaterEqual(len(site.areas), 4)
        for p in site.props + site.areas:
            self.assertTrue(inside_box(roof, p["x"], p["y"]), p)

    def test_long_canopy_tiles_kits(self):
        roof = rect("roof", 0.0, 22.0, 46.0, 18.0, cls="roof")
        site = gs.layout(STATION, [SHOP, roof], ROAD)
        self.assertEqual(len([p for p in site.props if "s_fuel_structure_" in p["shape"]]), 2)

    def test_narrow_canopy_pumps_clear_of_the_shop(self):
        # review I3: a kit scaled down no further than KIT_SCALE[0] put pumps past a narrow canopy, into the shop
        roof = rect("roof", 0.0, 32.0, 26.0, 6.0, cls="roof")
        site = gs.layout(STATION, [SHOP, roof], ROAD)
        self.assertTrue(site.areas)
        for a in site.areas:
            self.assertFalse(inside_box(SHOP, a["x"], a["y"], pad=gs.BLD_CLEAR_M + a["half"][0]), a)

    def test_no_canopy_pump_pair(self):
        site = gs.layout(STATION, [SHOP], ROAD)
        self.assertEqual(site.drop_ids, ())
        pumps = [p for p in site.props if "s_fuel_pump_" in p["shape"] and p["collide"]]
        self.assertEqual(len(pumps), 2)
        self.assertEqual(len(site.areas), 2)
        for p in pumps:
            self.assertTrue(5.0 < p["y"] < 36.0, p)                     # between the road and the shop's front
        self.assertAlmostEqual(pumps[0]["y"], pumps[1]["y"], places=6)  # a row parallel to the shop's front

    def test_pin_on_road_pumps_off_road(self):
        # a Sunoco near Pittsburgh: Microsoft's pin on the road beside the station
        st = gs.Station("sun", 3.0, 0.0, "Sunoco Gas Station", "", ("p",), True)
        shop = rect("shop", 0.0, 30.0, 12.0, 10.0)
        site = gs.layout(st, [shop], ROAD)
        self.assertIsNotNone(site)
        for p in site.props:
            self.assertGreater(road_gap(p["x"], p["y"]), gs.ROAD_CLEAR_M, p)
            self.assertFalse(inside_box(shop, p["x"], p["y"], pad=gs.BLD_CLEAR_M), p)

    def test_no_room_no_site(self):
        shop = rect("shop", 0.0, 13.0, 20.0, 10.0)          # 3 m between the road's edge and the shop
        st = gs.Station("st", 0.0, 13.0, "Shell", "Shell", ("p",), True)
        self.assertIsNone(gs.layout(st, [shop], ROAD))

    def test_no_shop_no_site(self):
        st = gs.Station("st", 0.0, 200.0, "Shell", "Shell", ("p",), True)
        self.assertIsNone(gs.layout(st, [SHOP], ROAD))

    def test_conv_brand_needs_canopy(self):
        st = gs.Station("wawa", 0.0, 45.0, "Wawa", "Wawa", ("p",), False)
        self.assertIsNone(gs.layout(st, [SHOP], ROAD))
        can = rect("ml", 0.0, 20.0, 28.0, 18.0, src=ML, height=1.48)
        self.assertIsNotNone(gs.layout(st, [SHOP, can], ROAD))

    def test_kit_hashed(self):
        roof = rect("roof", 0.0, 22.0, 30.0, 21.0, cls="roof")

        def kit(sid):
            st = gs.Station(sid, 0.0, 45.0, "x", "", ("p",), True)
            return next(p["shape"] for p in gs.layout(st, [SHOP, roof], ROAD).props if "structure" in p["shape"])
        self.assertEqual(kit("abc"), kit("abc"))
        self.assertEqual({kit(f"s{i}") for i in range(200)}, {k["shape"] for k in gs.KITS})


# --- the road tile: map data in, station records and the canopies to leave out ------------------------------------
import json  # noqa: E402
import tempfile  # noqa: E402

import geodesy  # noqa: E402
import mapgeom as mg  # noqa: E402
import roads  # noqa: E402

FRAME = geodesy.EnuFrame(40.44, -80.00)


def _ll(xy):
    xy = np.asarray(xy, np.float64)
    la, lo, _ = FRAME.enu_to_geodetic(xy[:, 0], xy[:, 1], 0.0)
    return np.stack([lo, la], 1)


def feats(layer, rows, kind):
    """mapgeom.Features of rows [(id, xy (k, 2) or (x, y), {attr: value})], sorted by id."""
    rows = sorted(rows, key=lambda r: r[0])
    geoms = []
    for _, xy, _ in rows:
        xy = np.asarray(xy, np.float64).reshape(-1, 2)
        geoms.append((kind, [[_ll(xy)]]))
    f = mg.build(layer, np.array([r[0] for r in rows]), geoms)
    keys = sorted({k for r in rows for k in r[2]})
    for k in keys:
        vals = [r[2].get(k) for r in rows]
        if any(isinstance(v, str) for v in vals):
            f.attrs[k] = np.array([v if v is not None else "" for v in vals], object)
        else:
            f.attrs[k] = np.array([np.nan if v is None else v for v in vals], np.float64)
    return f


def box_ring(cx, cy, w, h):
    return [(cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2), (cx + w / 2, cy + h / 2), (cx - w / 2, cy + h / 2),
            (cx - w / 2, cy - h / 2)]


class FakeCell:
    def __init__(self, layers):
        self.layers = layers


class FakeMD:
    """Every map cell holds the same layers (the code takes a feature in two cells once, by id)."""

    def __init__(self, layers, fail=()):
        self.all, self.fail = layers, set(fail)

    def cell(self, key, layers=None, kind="demand"):
        for name in layers or ():
            if name in self.fail:
                raise IOError(f"no {name}")
        return FakeCell({k: self.all.get(k) for k in (layers or ())})


def station_md(fail=()):
    """A station on tile (0, 0)'s east edge: pin and shop in tile (0, 0), the canopy's centroid in tile (1, 0), the
    road beyond it (x = 320)."""
    x0, _, x1, _ = roads.tile_bbox(0, 0)
    assert (x0, x1) == (0.0, roads.TILE)
    places = feats("places", [("pla-1", (240.0, 100.0), {"basic_category": "gas_station", "name": "Speedway",
                                                         "operating_status": "open"})], mg.POINT)
    blds = feats("buildings", [("b-shop", box_ring(230.0, 100.0, 18.0, 20.0), {"class": "retail", "src_dataset": "OpenStreetMap"}),
                               ("b-roof", box_ring(275.0, 100.0, 21.0, 30.0), {"class": "roof", "src_dataset": "OpenStreetMap"})],
                 mg.POLYGON)
    rds = feats("roads", [("r-1", [(320.0, -200.0), (320.0, 400.0)], {"class": "secondary"})], mg.LINE)
    return FakeMD({"places": places, "buildings": blds, "roads": rds}, fail)


def flat(la, lo):
    return np.zeros(np.shape(la))


class TileTest(unittest.TestCase):
    def test_canopy_dropped_by_owning_tile_only(self):
        md = station_md()
        recs, skip = gs.tile_sites(FRAME, 0, 0, md, flat)
        self.assertEqual(len(recs), 1)
        self.assertIn("b-roof", skip)
        r = recs[0]
        self.assertEqual(r["name"], "Speedway")
        self.assertTrue(r["areas"] and all(len(a["pos"]) == 3 and len(a["icon"]) == 3 for a in r["areas"]))
        self.assertTrue(all(len(p["pos"]) == 3 for p in r["props"]))
        for a in r["areas"]:                                    # under the roof (x 264.5..285.5)
            self.assertTrue(262.0 < a["pos"][0] < 288.0, a)
            self.assertGreater(a["icon"][2], a["pos"][2])
        recs1, skip1 = gs.tile_sites(FRAME, 1, 0, md, flat)
        self.assertEqual(recs1, [])
        self.assertIn("b-roof", skip1)
        json.dumps(recs)                                        # plain JSON

    def test_no_places_layer_no_stations(self):
        self.assertEqual(gs.tile_sites(FRAME, 0, 0, station_md(fail=("places",)), flat), ([], frozenset()))

    def test_buildings_near_and_drive_lines(self):
        md = station_md()
        blds = gs.buildings_near(FRAME, [md.all["buildings"], md.all["buildings"]], 240.0, 100.0, 80.0)
        self.assertEqual(sorted(b.id for b in blds), ["b-roof", "b-shop"])     # once each
        roof = next(b for b in blds if b.id == "b-roof")
        self.assertEqual((roof.cls, roof.src), ("roof", "OpenStreetMap"))
        self.assertAlmostEqual(float(roof.ring[:-1, 0].mean()), 275.0, delta=0.05)
        lines = gs.drive_lines(FRAME, [md.all["roads"]], 240.0, 100.0, 200.0)
        self.assertEqual(len(lines), 1)
        self.assertGreater(lines[0][1], 3.0)

    def test_one_station_per_shop(self):
        # unmerged pins of one station (a brand pin and the shop's own name) lay out the same forecourt once
        md = station_md()
        extra = feats("places", [("pla-1", (240.0, 100.0), {"basic_category": "gas_station", "name": "Speedway",
                                                           "operating_status": "open"}),
                                 ("pla-2", (232.0, 108.0), {"basic_category": "gas_station", "name": "A-Plus Mini Mart",
                                                           "operating_status": "open"})], mg.POINT)
        md.all["places"] = extra
        recs, _ = gs.tile_sites(FRAME, 0, 0, md, flat)
        self.assertEqual(len(recs), 1)

    def test_drive_lines_use_lanes(self):
        # review I5: a six-lane arterial is wider than its class's default
        rds = feats("roads", [("r-1", [(0.0, 0.0), (300.0, 0.0)], {"class": "primary"}),
                              ("r-2", [(0.0, 50.0), (300.0, 50.0)], {"class": "primary", "osm_lanes": 6.0})], mg.LINE)   # (osmtags.join)
        half = {round(float(l[0, 1])): h for l, h in gs.drive_lines(FRAME, [rds], 150.0, 25.0, 100.0)}
        self.assertGreater(half[50], half[0] + 2.0)

    def test_tile_buildings_skip(self):
        import buildings
        md = station_md()
        a = buildings.tile_buildings(FRAME, 0, 0, 512, 256, [md.all["buildings"]], flat)
        b = buildings.tile_buildings(FRAME, 0, 0, 512, 256, [md.all["buildings"]], flat, skip=frozenset({"b-roof"}))
        self.assertEqual(len(a) - len(b), 1)

    def test_write_tile_fuel(self):
        rec = {"id": "x", "props": [], "areas": []}
        chunk = {"m": "x", "w": 1, "tl": 1, "p": 1, "n": [[0, 0, 0], [1, 0, 0]]}
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = roads.write_tile(tmp, "/vfs", 1, 0, 0, [chunk], fuel=[rec])
            data = json.loads(Path(tmp, Path(path).name).read_text())
            self.assertEqual(data["fuel"], [rec])
            path, _ = roads.write_tile(tmp, "/vfs", 1, 0, 1, [chunk], fuel=[])
            self.assertNotIn("fuel", json.loads(Path(tmp, Path(path).name).read_text()))

    def test_clear_park(self):
        rec = {"props": [{"pos": [100.0, 0.0, 0.0]}], "areas": [{"pos": [50.0, 0.0, 0.0], "half": [1.6, 2.2, 2.75]}]}
        park = [[52.0, 1.0, 0.0, 0.0], [101.5, 0.0, 0.0, 0.0], [70.0, 0.0, 0.0, 0.0]]
        self.assertEqual(gs.clear_park(park, [rec]), [[70.0, 0.0, 0.0, 0.0]])
        self.assertEqual(gs.clear_park(park, []), park)

    def test_flag_default_off_recommended_on(self):
        import earthd
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        try:
            import start
        finally:
            sys.path.pop(0)
        import inspect
        self.assertIs(inspect.signature(earthd.Server).parameters["gas_stations"].default, False)
        self.assertIn('ap.add_argument("--gas-stations", choices=("on", "off"), default="off"',
                      Path(earthd.__file__).read_text(encoding="utf-8"))
        self.assertIn(("--gas-stations", "on"), start.RECOMMENDED)


REAL = __import__("os").environ.get("BNGE_GAS_REAL") == "1"


@unittest.skipUnless(REAL, "set BNGE_GAS_REAL=1 to lay out the user's three test stations from the real Overture data")
class RealSpotsTest(unittest.TestCase):
    """The stations the user checked on satellite view, 2026-10-23 (the Pittsburgh area)."""

    @classmethod
    def setUpClass(cls):
        import mapdata
        import netfetch
        import overture
        cache = mapdata.DEFAULT_CACHE
        cls.md = mapdata.MapData(cache, netfetch.Fetcher(stats_dir=__import__("os").path.join(cache, "netstats")),
                                 overture.RELEASE)

    def site(self, lat, lon):
        frame = geodesy.EnuFrame(lat, lon)
        la, lo, _ = frame.enu_to_geodetic(-128.0, -128.0, 0.0)     # the pin in the middle of tile (0, 0)
        frame = geodesy.EnuFrame(float(la), float(lo))
        recs, skip = gs.tile_sites(frame, 0, 0, self.md, flat)
        self.assertEqual(len(recs), 1, recs)
        return recs[0], skip

    def test_speedway_tagged_canopy(self):
        rec, skip = self.site(40.403093, -80.041343)
        self.assertEqual(len(skip), 1, "the OSM roof left out")
        self.assertTrue(any("s_fuel_structure_" in p["shape"] for p in rec["props"]))
        self.assertGreaterEqual(len(rec["areas"]), 4)

    def test_sheetz_ml_canopy(self):
        rec, skip = self.site(40.861281, -79.900648)
        self.assertEqual(len(skip), 1, "the imagery outline left out")
        self.assertTrue(any("s_fuel_structure_" in p["shape"] for p in rec["props"]))

    def test_sunoco_pin_on_road(self):
        # the pin on the road; the store is the mapper's 190 m2 building; the untagged 179 m2 imagery outline between it
        # and the road is the canopy over the pumps (2026-10-23)
        rec, skip = self.site(40.465627, -79.955447)
        self.assertEqual(len(skip), 1, "the imagery canopy (the user confirmed it on satellite view, 2026-10-23)")
        self.assertTrue(any("s_fuel_structure_" in p["shape"] for p in rec["props"]))


if __name__ == "__main__":
    unittest.main()
