"""markings: country styles, real dashes and the lines lane by lane from OpenStreetMap's lane tags."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import markings  # noqa: E402
import roads  # noqa: E402


class StyleTest(unittest.TestCase):
    def test_countries(self):
        self.assertEqual(markings.style("US").centre, "line_yellow")
        self.assertEqual(markings.style("NO").centre, "line_yellow", "Norway paints its centre lines yellow")
        self.assertEqual(markings.style("DE").centre_rural, (6.0, 12.0))
        self.assertEqual(markings.style("GB").lane_urban, (1.0, 5.0))
        self.assertEqual(markings.style(None, -100.0).centre, "line_yellow", "unknown: by longitude")
        self.assertEqual(markings.style(None, 10.0).centre, "line_white")


class DashTest(unittest.TestCase):
    def test_dashes_follow_the_line_and_its_phase(self):
        pts = np.array([[0.0, 0, 0], [10, 0, 1], [10, 20, 2]])
        d = markings.dashes(pts, (3.0, 9.0))
        self.assertEqual(len(d), 3)
        np.testing.assert_allclose(d[0][[0, -1]], [[0, 0, 0], [3, 0, 0.3]])
        np.testing.assert_allclose(d[1][[0, -1], :2], [[10, 2], [10, 5]], err_msg="round the corner")
        moved = markings.dashes(pts, (3.0, 9.0), phase=11.0)
        np.testing.assert_allclose(moved[0][0, :2], [1, 0], err_msg="the cycle 11 m on: 1 m into the next dash")


class LanePlanTest(unittest.TestCase):
    def test_none_without_tags(self):
        self.assertIsNone(markings.lane_plan({}, True, 7.0, 2))
        self.assertIsNone(markings.lane_plan(None, True, 7.0, 2))

    def test_uneven_directions_move_the_centre_line(self):
        plan = markings.lane_plan({"lanes:forward": "2", "lanes:backward": "1"}, True, 9.9, 3)
        self.assertEqual([r for _, r, _ in plan], ["centre", "lane"])
        self.assertAlmostEqual(plan[0][0], 1.65, "one lane left of it, two right (right-hand traffic)")
        lh = markings.lane_plan({"lanes:forward": "2", "lanes:backward": "1"}, True, 9.9, 3, left_hand=True)
        self.assertAlmostEqual(lh[-1][0], -1.65, msg="driving on the left: mirrored")

    def test_two_way_turn_lane_and_passing(self):
        plan = markings.lane_plan({"lanes:both_ways": "1"}, True, 15.0, 5)
        self.assertEqual([(round(o, 2), r, h) for o, r, h in plan],
                         [(4.5, "lane", False), (1.5, "twltl", 1), (-1.5, "twltl", -1), (-4.5, "lane", False)])
        self.assertEqual(markings.lane_plan({"overtaking": "no"}, True, 7.0, 2)[0][2], "double")
        self.assertEqual(markings.lane_plan({"overtaking:forward": "no"}, True, 7.0, 2)[0][2], "solid_right")

    def test_change_lanes_makes_lines_solid(self):
        plan = markings.lane_plan({"change:lanes": "no|yes|yes"}, False, 10.5, 3)
        self.assertEqual([h for _, _, h in plan], [True, False])

    def test_extras_draws_the_plan(self):
        sp = roads.Spec(10.0, "paved", False, "primary", False)
        line = np.array([[0.0, 0, 0], [100, 0, 0]])
        lk = [(lk.get("kind"), round(float(ln[0][1]), 2)) for lk, w, ln in
              roads.extras(sp, line, -76.0, country="US", osm={"lanes:forward": "1", "lanes:backward": "1",
                                                                "overtaking": "no"}) if lk.get("kind") == "centre"]
        self.assertEqual(sorted(set(lk)), [("centre", -0.12), ("centre", 0.12)], "a double solid yellow line")


class AiLanesTest(unittest.TestCase):
    def test_the_ai_drives_the_painted_lanes(self):
        import navgraph
        for sp in (roads.Spec(12.8, "paved", True, "tertiary", False), roads.Spec(13.2, "paved", False, "primary", False),
                   roads.Spec(11.0, "paved", False, "motorway", True, 2.5, 1)):
            self.assertAlmostEqual(navgraph.driving_width(sp), roads.lane_layout(sp)[0])


if __name__ == "__main__":
    unittest.main()
