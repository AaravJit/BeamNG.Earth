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
        # a Sheetz near Pittsburgh: a convenience store by its category, fuel by its brand
        self.assertEqual(gs.classify("convenience_store", "convenience_store", "Q7492551", "open"), (False, False, True))
        # "7-ELEVEN/SPEEDWAY #40001": Western Union by its category, no brand
        self.assertIsNone(gs.classify("financial_service", "money_transfer_service", "", "open"))
        self.assertEqual(gs.classify("atm", "atm", "Q458363", "open"), (False, True, False))     # Marathon
        self.assertIsNone(gs.classify("gas_station", "gas_station", "", "closed"))
        self.assertIsNone(gs.classify("gas_station", "gas_station", "", "permanently_closed"))
        self.assertEqual(gs.classify("gas_station", "gas_station", "Q458363", "open"), (True, True, False))

    def test_brand_lists_disjoint(self):
        self.assertFalse(set(gs.FUEL_BRANDS) & set(gs.CONV_BRANDS))
        self.assertIn("Q7492551", gs.CONV_BRANDS)       # Sheetz
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

    def test_no_shop_far(self):
        self.assertIsNone(gs.find_shop(STATION, [rect("far", 300.0, 300.0, 20.0, 20.0)]))


if __name__ == "__main__":
    unittest.main()
