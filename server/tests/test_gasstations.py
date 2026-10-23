"""gasstations: which Overture places are fuel stations (categories, two brand lists), their pins merged into one
station, the shop and the canopy found among the buildings, and the forecourt laid out clear of roads and buildings
(the user, 2026-10-23: "the refueling is the main part")."""
import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
