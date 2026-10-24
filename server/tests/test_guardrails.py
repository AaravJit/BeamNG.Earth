"""Mapped guardrail geometry, stock Italy model rows, and Overture tag filtering."""
import json
import os
import sys
import unittest
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import geodesy
import guardrails
import mapgeom
import roads
import roadmesh


class FakeFeatures:
    def __init__(self, attrs):
        self.attrs = attrs


class GuardrailTagTest(unittest.TestCase):
    def test_normalized_overture_guardrail(self):
        f = FakeFeatures({"class": np.array(["guard_rail"]), "subtype": np.array(["barrier"])})
        self.assertTrue(guardrails._is_guardrail(f, 0))

    def test_osm_tag_fallback(self):
        f = FakeFeatures({"class": np.array(["barrier"]), "subtype": np.array(["barrier"]),
                          "tags": np.array([json.dumps({"barrier": "guard_rail"})])})
        self.assertTrue(guardrails._is_guardrail(f, 0))

    def test_other_barriers_are_not_guardrails(self):
        f = FakeFeatures({"class": np.array(["fence"]), "subtype": np.array(["barrier"]),
                          "tags": np.array([json.dumps({"barrier": "fence"})])})
        self.assertFalse(guardrails._is_guardrail(f, 0))

    def test_invalid_tags_are_ignored(self):
        f = FakeFeatures({"class": np.array(["barrier"]), "subtype": np.array(["barrier"]),
                          "tags": np.array(["not json"])})
        self.assertFalse(guardrails._is_guardrail(f, 0))

    def test_non_object_json_tags_are_ignored(self):
        # Overture source_tags is optional; malformed / non-object JSON must not abort
        # guardrail generation for the whole road tile.
        for raw in ('null', '[]', '42', '"text"'):
            with self.subTest(raw=raw):
                f = FakeFeatures({"class": np.array(["barrier"]), "subtype": np.array(["barrier"]),
                                  "tags": np.array([raw])})
                self.assertFalse(guardrails._is_guardrail(f, 0))


class GuardrailStockAssetTest(unittest.TestCase):
    def test_stock_italy_guardrail_model_is_registered_as_forest_type(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(root, "mod", "beamng_earth", "lua", "ge", "extensions",
                            "beamngEarth", "trees.lua")
        with open(path, encoding="utf-8") as f:
            source = f.read()
        self.assertIn("M.CATALOG.guardrail", source)
        self.assertIn("/art/shapes/objects/italy_guardrails_basic", source)
        self.assertIn("/art/shapes/objects/italy_guardrails_basic_end_cw", source)
        self.assertIn("/art/shapes/objects/italy_guardrails_basic_end_ccw", source)
        self.assertNotIn("M.NO_COLLIDE.guardrail = true", source)
        self.assertIn("M.GROUP_MAX_M.guardrail = 4000", source)
        self.assertIn("m:setColumn(0, vec3(row[9], row[10], row[11]))", source)
        self.assertIn("local isGuardrail = g and g:sub(1, 9) == 'guardrail'", source)
        self.assertIn("elseif not rowCap or #t.rows <= rowCap then", source)


class GuardrailForestTest(unittest.TestCase):
    def setUp(self):
        self.frame = geodesy.EnuFrame(39.2834, -76.5934, 0.0)

    def features(self, cls, tags=None, xy=None):
        xy = np.array(xy if xy is not None else [[10.0, 50.0], [90.0, 50.0]], np.float64)
        lat, lon, _ = self.frame.enu_to_geodetic(xy[:, 0], xy[:, 1], np.zeros(2))
        attrs = {"subtype": np.array(["barrier"]), "class": np.array([cls])}
        if tags is not None:
            attrs["tags"] = np.array([json.dumps(tags)])
        return mapgeom.Features(
            layer="infrastructure",
            ids=np.array(["guard-1"]),
            kind=np.array([mapgeom.LINE], np.uint8),
            part_off=np.array([0, 1], np.int64),
            ring_off=np.array([0, 1], np.int64),
            coord_off=np.array([0, 2], np.int64),
            lonlat=np.column_stack((lon, lat)),
            attrs=attrs,
        )

    def test_two_sided_osm_tag(self):
        feats = self.features("guard_rail", tags={"two_sided": "yes"})
        self.assertTrue(guardrails._is_two_sided(feats, 0))
        for value in ("no", "false", ""):
            one = self.features("guard_rail", tags={"two_sided": value})
            self.assertFalse(guardrails._is_two_sided(one, 0))

    def test_stock_guardrail_rows_are_exported_in_road_tile_json(self):
        rows = [[0, 300.0, 600.0, 12.0, 0.0, 0.0, 0.9, 0.0]]
        with __import__("tempfile").TemporaryDirectory() as tmp:
            path, count = roads.write_tile(tmp, "vfs", 7, 1, 2, [], guardrail_rows=rows)
            with open(os.path.join(tmp, "roads_7_1_2.json"), encoding="utf-8") as f:
                data = json.load(f)
        self.assertEqual(count, 0)
        self.assertEqual(path, "vfs/roads_7_1_2.json")
        self.assertEqual(data["guardrails"], {"items": rows,
                                               "groups": ["guardrail", "guardrail_start", "guardrail_end",
                                                          "guardrail_back", "guardrail_back_start", "guardrail_back_end"]})
        self.assertNotIn("guardrail", roads.shape_files(7, 1, 2))

    def test_stock_guardrail_rows_follow_ground_and_have_full_orientation(self):
        feats = self.features("guard_rail", xy=[[10.0, 50.0], [90.0, 50.0]])
        rows = guardrails.forest_rows(self.frame, lambda _key: feats, (0.0, 0.0, 100.0, 100.0),
                                      lambda lat, lon: np.full(np.shape(lat), 12.0, np.float64))
        self.assertTrue(rows)
        for row in rows:
            self.assertIn(row[0], (0, 1, 2))
            self.assertAlmostEqual(row[3], 12.0, delta=0.05)
            self.assertAlmostEqual(row[4], 0.0, delta=0.1)
            self.assertEqual(row[6], guardrails.MODULE_HEIGHT_M)
            self.assertGreaterEqual(row[7], 0.0)
            self.assertLess(row[7], 1.0)
            self.assertEqual(len(row), 17)
            np.testing.assert_allclose(row[8:17], [1,0,0, 0,1,0, 0,0,1], atol=1e-3)
        groups = [row[0] for row in rows]
        self.assertIn(1, groups)
        self.assertIn(2, groups)
        centres = [row[1] for row in rows if row[0] == 0]
        self.assertGreaterEqual(len(centres), 2)
        for a, b in zip(centres, centres[1:]):
            self.assertAlmostEqual(b - a, guardrails.MODULE_STEP_M, places=4)

    def test_two_sided_guardrails_create_reversed_instances(self):
        feats = self.features("guard_rail", tags={"two_sided": "yes"},
                              xy=[[10.0, 50.0], [90.0, 50.0]])
        rows = guardrails.forest_rows(self.frame, lambda _key: feats, (0.0, 0.0, 100.0, 100.0),
                                      lambda lat, lon: np.full(np.shape(lat), 12.0, np.float64))
        groups = [row[0] for row in rows]
        self.assertEqual(groups.count(0), groups.count(3))
        self.assertEqual(groups.count(1), groups.count(4))
        self.assertEqual(groups.count(2), groups.count(5))
        one = next(row for row in rows if row[0] == 0)
        back = next(row for row in rows if row[0] == 3)
        # The two models must be parallel, separated, and face away from the centre gap.
        np.testing.assert_allclose(back[8:14], [-v for v in one[8:14]], atol=1e-4)
        np.testing.assert_allclose(back[14:17], one[14:17], atol=1e-4)
        self.assertAlmostEqual(abs(one[2] - back[2]), guardrails.TWO_SIDED_SPACING_M, places=3)
        self.assertAlmostEqual(0.5 * (one[2] + back[2]), 50.0, places=2)
        self.assertLess((one[2] - 50.0) * one[12], 0.0)
        self.assertLess((back[2] - 50.0) * back[12], 0.0)

    def test_centre_guardrail_remains_on_bridge_and_outer_edge_rails_are_removed(self):
        deck = {"m": "road_asphalt_2lane", "w": 8.0, "tl": 11.0, "p": 20,
                "deck": True, "fixed": True, "fid": "bridge",
                "n": [[0.0, 0.0, 20.0], [100.0, 0.0, 20.0]]}
        decks = [{"n": deck["n"], "w": deck["w"]}]
        parapets = roadmesh.parapet_runs([deck])
        self.assertEqual(len(parapets), 2)
        outer = max(parapets, key=lambda item: float(np.mean(item["line"][:, 1])))
        y = float(np.mean(outer["line"][:, 1]))
        upper_elev = lambda lat, lon: np.full(np.shape(lat), 20.0, np.float64)
        exact = (0.0, -20.0, 100.0, 20.0)

        # A central two-sided divider must stay on the bridge; the old 5 m XY filter
        # incorrectly removed it together with the roadside rails.
        centre = self.features("guard_rail", tags={"two_sided": "yes", "bridge": "yes"},
                               xy=[[10.0, 0.0], [90.0, 0.0]])
        centre_rows = guardrails.forest_rows(
            self.frame, lambda _key: centre, exact, upper_elev,
            decks=decks, parapets=parapets)
        self.assertTrue(centre_rows, "the mapped centre divider must render on the bridge")
        self.assertTrue(any(row[0] == 0 for row in centre_rows))
        self.assertTrue(any(row[0] == 3 for row in centre_rows))
        for row in centre_rows:
            self.assertAlmostEqual(row[3], 20.0, delta=0.05)

        # Rails on/near the outer parapets are redundant with the concrete walls and are hidden.
        for line_y in (y, 5.0):
            with self.subTest(line_y=line_y):
                edge = self.features(
                    "guard_rail", tags={"two_sided": "yes", "bridge": "yes"},
                    xy=[[10.0, line_y], [90.0, line_y]])
                edge_rows = guardrails.forest_rows(
                    self.frame, lambda _key: edge, exact, upper_elev,
                    decks=decks, parapets=parapets)
                self.assertEqual(edge_rows, [],
                                 "outer mapped rails should be replaced by the concrete parapet")

        # Do not delete a lower road's mapped rail just because it overlaps an upper parapet in XY.
        lower_elev = lambda lat, lon: np.full(np.shape(lat), 12.0, np.float64)
        underpass = self.features("guard_rail", tags={"two_sided": "yes", "layer": "-1"},
                                  xy=[[10.0, y], [90.0, y]])
        lower_rows = guardrails.forest_rows(
            self.frame, lambda _key: underpass, exact, lower_elev,
            decks=decks, parapets=parapets)
        self.assertTrue(lower_rows, "the lower road's rail must survive the XY overlap")
        for row in lower_rows:
            self.assertAlmostEqual(row[3], 12.0, delta=0.1)


    def test_guardrails_pitch_to_the_ground_grade(self):
        feats = self.features("guard_rail", xy=[[10.0, 50.0], [90.0, 50.0]])
        # A stable east-west grade; longitude is nearly proportional to east distance here.
        elev = lambda lat, lon: 12.0 + (np.asarray(lon) + 76.5934) * 8600.0
        rows = guardrails.forest_rows(self.frame, lambda _key: feats, (0.0, 0.0, 100.0, 100.0), elev)
        segment = next(row for row in rows if row[0] == 0)
        self.assertGreater(segment[10], 0.0)
        self.assertLess(segment[10], 0.5)



if __name__ == "__main__":
    unittest.main()
