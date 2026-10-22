"""osmtags.py: OpenStreetMap's road tags (lanes, width, speed limit...) joined to Overture's road segments by way id,
and the roads using them (roads.road_specs / carriageway, navgraph lanes)."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import navgraph  # noqa: E402
import osmtags  # noqa: E402
import roads  # noqa: E402
from test_roads import features  # noqa: E402


def tagged(lines, classes, records, tags):
    f = features(lines, classes)
    f.attrs["src_record"] = np.array(records)
    return osmtags.join(f, tags)


class JoinTest(unittest.TestCase):
    def test_way_id(self):
        self.assertEqual(osmtags.way_id("w1360453999@2"), 1360453999)
        self.assertIsNone(osmtags.way_id("n12@1"))
        self.assertIsNone(osmtags.way_id(""))
        self.assertIsNone(osmtags.way_id(None))

    def test_join_by_way_id(self):
        f = tagged([[(0, 0), (100, 0)], [(0, 20), (100, 20)], [(0, 40), (100, 40)]], ["primary"] * 3,
                   ["w5@3", "w6@1", "t-tomtom"], {5: {"lanes": 4, "width": 13.5, "sidewalk": "both"}, 6: {}})
        self.assertEqual(f.attrs["osm_lanes"][0], 4)
        self.assertTrue(np.isnan(f.attrs["osm_lanes"][1]) and np.isnan(f.attrs["osm_lanes"][2]))
        self.assertEqual(str(f.attrs["osm_sidewalk"][0]), "both")
        self.assertEqual(str(f.attrs["osm_sidewalk"][2]), "")


class SpecTest(unittest.TestCase):
    def test_lanes_set_the_width_and_the_markings(self):
        f = tagged([[(0, 0), (200, 0)], [(0, 30), (200, 30)]], ["secondary", "secondary"], ["w1@1", "w2@1"],
                   {1: {"lanes": 4}})
        a, b = roads.road_specs(f)
        self.assertEqual(a.lanes, 4)
        self.assertIsNone(b.lanes)
        self.assertGreaterEqual(a.width, 4 * roads.LANE_W["secondary"], "four lanes wide")
        self.assertGreater(a.width, b.width, "wider than the class's guess")

    def test_osm_width_is_the_width(self):
        f = tagged([[(0, 0), (200, 0)]], ["residential"], ["w1@1"], {1: {"width": 9.0, "lanes": 2}})
        self.assertAlmostEqual(roads.road_specs(f)[0].width, 9.0)

    def test_nonsense_is_not_believed(self):
        f = tagged([[(0, 0), (200, 0)]], ["residential"], ["w1@1"], {1: {"width": 400.0, "lanes": 40}})
        sp = roads.road_specs(f)[0]
        self.assertIsNone(sp.lanes)
        self.assertLess(sp.width, 40.0)

    def test_maxspeed_fills_a_missing_limit(self):
        f = tagged([[(0, 0), (200, 0)]], ["residential"], ["w1@1"], {1: {"maxspeed": 40.2336}})
        self.assertAlmostEqual(roads.road_specs(f)[0].kmh, 40.2336)

    def test_navgraph_takes_the_lane_count(self):
        self.assertEqual(navgraph._lanes(7.0, False, 4), (2, 2))
        self.assertEqual(navgraph._lanes(7.0, True, 3), (3, 0))
        self.assertEqual(navgraph._lanes(7.0, False), navgraph._lanes(7.0, False, None))


if __name__ == "__main__":
    unittest.main()
