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


class BridgeFootprintTest(unittest.TestCase):
    deck = {"n": [[0.0, 0.0, 20.0], [100.0, 0.0, 20.0]], "w": 8.0}

    def on(self, a, b):
        return guardrails._on_bridge(a, b, guardrails._bridge_footprint_segments([self.deck]))

    def test_footprint_reaches_a_small_margin_past_the_deck_edge(self):
        # Half width 4 m + 1.5 m clearance: a rail 5.4 m off the axis is on the bridge, one at 5.6 m is not.
        self.assertTrue(self.on([10.0, 5.4], [13.0, 5.4]))
        self.assertFalse(self.on([10.0, 5.6], [13.0, 5.6]))
        self.assertFalse(self.on([10.0, 9.0], [13.0, 9.0]))        # the old 5 m buffer would have hidden this

    def test_deck_ends_are_flat_not_round(self):
        # Beyond the end the old buffer reached 5 m + half the width; now only a 0.5 m margin.
        self.assertFalse(self.on([103.0, 0.0], [106.0, 0.0]))
        self.assertFalse(self.on([100.6 + 1.5, 4.0], [100.6 + 4.5, 4.0]))
        self.assertTrue(self.on([96.0, 4.0], [99.0, 4.0]))

    def test_module_overlapping_the_end_is_kept_by_its_middle(self):
        self.assertTrue(self.on([97.5, 4.0], [100.5, 4.0]))        # middle at 99.0: on the deck
        self.assertFalse(self.on([100.6, 4.0], [103.6, 4.0]))      # middle at 102.1: on the approach


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

    def test_guardrails_pitch_to_the_ground_grade(self):
        feats = self.features("guard_rail", xy=[[10.0, 50.0], [90.0, 50.0]])
        # A stable east-west grade; longitude is nearly proportional to east distance here.
        elev = lambda lat, lon: 12.0 + (np.asarray(lon) + 76.5934) * 8600.0
        rows = guardrails.forest_rows(self.frame, lambda _key: feats, (0.0, 0.0, 100.0, 100.0), elev)
        segment = next(row for row in rows if row[0] == 0)
        self.assertGreater(segment[10], 0.0)
        self.assertLess(segment[10], 0.5)


    def deck_fixture(self):
        deck = {"m": "road_asphalt_2lane", "w": 8.0, "tl": 11.0, "p": 20,
                "deck": True, "fixed": True, "fid": "bridge",
                "n": [[0.0, 0.0, 20.0], [100.0, 0.0, 20.0]]}
        decks = [{"n": deck["n"], "w": deck["w"]}]
        return decks, roadmesh.parapet_runs([deck])

    def test_mapped_rails_on_the_deck_are_dropped_but_rails_beside_it_stay(self):
        decks, parapets = self.deck_fixture()
        upper = lambda lat, lon: np.full(np.shape(lat), 20.0, np.float64)
        box = (0.0, -20.0, 100.0, 20.0)
        for tags, line_y in (({"bridge": "yes"}, 4.5), ({"two_sided": "yes"}, 0.0), ({}, 5.0)):
            with self.subTest(on_deck=line_y):
                f = self.features("guard_rail", tags=tags, xy=[[10.0, line_y], [90.0, line_y]])
                self.assertEqual(guardrails.forest_rows(self.frame, lambda _k: f, box, upper,
                                                        decks=decks, parapets=parapets), [])
        # 6 m off the axis (2 m past the edge): an ordinary roadside rail, it stays at its terrain height.
        beside = self.features("guard_rail", xy=[[10.0, 6.5], [90.0, 6.5]])
        rows = guardrails.forest_rows(self.frame, lambda _k: beside, box,
                                      lambda lat, lon: np.full(np.shape(lat), 12.0, np.float64),
                                      decks=decks, parapets=parapets)
        self.assertTrue(rows)
        for row in rows:
            self.assertAlmostEqual(row[3], 12.0, delta=0.1)

    def test_approach_rails_continue_right_up_to_the_bridge_end(self):
        decks, parapets = self.deck_fixture()
        f = self.features("guard_rail", xy=[[100.0, 4.5], [140.0, 4.5]])
        rows = guardrails.forest_rows(self.frame, lambda _k: f, (0.0, -20.0, 200.0, 20.0),
                                      lambda lat, lon: np.full(np.shape(lat), 12.0, np.float64),
                                      decks=decks, parapets=parapets)
        self.assertTrue(rows)
        self.assertLess(min(r[1] for r in rows), 104.0, "the approach rail used to start 9 m after the bridge")

    def test_an_explicit_lower_level_rail_under_the_bridge_is_kept(self):
        decks, parapets = self.deck_fixture()
        f = self.features("guard_rail", tags={"layer": "-1"}, xy=[[10.0, 4.5], [90.0, 4.5]])
        rows = guardrails.forest_rows(self.frame, lambda _k: f, (0.0, -20.0, 100.0, 20.0),
                                      lambda lat, lon: np.full(np.shape(lat), 12.0, np.float64),
                                      decks=decks, parapets=parapets)
        self.assertTrue(rows)
        for row in rows:
            self.assertAlmostEqual(row[3], 12.0, delta=0.1)


class BridgeRailTest(unittest.TestCase):
    def runs(self, z1=20.0):
        deck = {"m": "road_asphalt_2lane", "w": 8.0, "tl": 11.0, "p": 20,
                "deck": True, "fixed": True, "fid": "bridge",
                "n": [[0.0, 0.0, 20.0], [100.0, 0.0, z1]]}
        return roadmesh.parapet_runs([deck])

    def test_one_rail_line_per_deck_edge_on_the_deck_edge_at_deck_height(self):
        runs = self.runs()
        rows = guardrails.bridge_rows(runs)
        self.assertTrue(rows)
        ys = sorted({round(r[2], 1) for r in rows})
        self.assertEqual(len(ys), 2)
        # the strip centre: half the road width plus half the 0.45 m parapet strip
        for y in ys:
            self.assertAlmostEqual(abs(y), 4.0 + roadmesh.PARAPET_W / 2, delta=0.1)
        for r in rows:
            self.assertEqual(len(r), 17)
            self.assertEqual(r[0], 0)
            self.assertAlmostEqual(r[3], 20.0, delta=1e-6)
            self.assertEqual(r[6], guardrails.MODULE_HEIGHT_M)

    def test_rails_cover_the_whole_run_without_gaps(self):
        rows = [r for r in guardrails.bridge_rows(self.runs()) if r[2] > 0]
        rows.sort(key=lambda r: r[1])
        xs = [r[1] for r in rows]
        half = guardrails.MODULE_LENGTH_M / 2
        self.assertAlmostEqual(xs[0] - half, 0.0, delta=0.6)
        self.assertAlmostEqual(xs[-1] + half, 100.0, delta=0.6)
        for a, b in zip(xs, xs[1:]):
            self.assertLessEqual(b - a, guardrails.MODULE_STEP_M + 1e-6)
            self.assertGreater(b - a, 1.0)

    def test_visible_face_looks_at_the_road_on_both_edges(self):
        # The model's visible side is opposite its back axis: the back must point out of the bridge.
        for r in guardrails.bridge_rows(self.runs()):
            outward = 1.0 if r[2] > 0 else -1.0
            self.assertGreater(r[12] * outward, 0.9, "back axis must point out of the bridge")

    def test_rails_pitch_with_the_deck_grade(self):
        rows = guardrails.bridge_rows(self.runs(z1=30.0))
        self.assertTrue(rows)
        for r in rows:
            self.assertAlmostEqual(r[10], 0.0995 * np.sign(r[8]), delta=0.01)   # a 10 % grade, either way round
            self.assertAlmostEqual(r[3], 20.0 + 0.1 * r[1], delta=0.1)

    def test_no_parapet_runs_no_rows(self):
        self.assertEqual(guardrails.bridge_rows([]), [])
        self.assertEqual(guardrails.bridge_rows(None), [])
        self.assertEqual(guardrails.bridge_rows([{"line": [[0, 0]], "z": [1], "out": [[0, 1]]}]), [])


class BridgeCurbTest(unittest.TestCase):
    def test_curb_height_replaces_the_wall_in_the_deck_mesh(self):
        deck = {"m": "road_asphalt_2lane", "w": 8.0, "tl": 11.0, "p": 20,
                "deck": True, "fixed": True, "fid": "bridge",
                "n": [[0.0, 0.0, 20.0], [100.0, 0.0, 20.0]]}
        wall = roadmesh.deck([deck], (0.0, 0.0, 0.0))
        curb = roadmesh.deck([deck], (0.0, 0.0, 0.0), parapet_h=roadmesh.BRIDGE_CURB_H)
        self.assertAlmostEqual(wall[0][:, 2].max() - 20.0, roadmesh.PARAPET_H, delta=1e-6)
        self.assertAlmostEqual(curb[0][:, 2].max() - 20.0, roadmesh.BRIDGE_CURB_H, delta=1e-6)



if __name__ == "__main__":
    unittest.main()
