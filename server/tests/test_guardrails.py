"""Mapped guardrail mesh geometry and Overture tag filtering."""
import json
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import geodesy
import guardrails
import mapgeom


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


class ClipSegmentTest(unittest.TestCase):
    def test_clips_to_tile(self):
        clipped = guardrails._clip_segment(np.array([-5.0, 5.0]), np.array([15.0, 5.0]),
                                           (0.0, 0.0, 10.0, 10.0))
        self.assertIsNotNone(clipped)
        np.testing.assert_allclose(clipped[0], [0.0, 5.0])
        np.testing.assert_allclose(clipped[1], [10.0, 5.0])

    def test_segment_outside_tile_is_ignored(self):
        self.assertIsNone(guardrails._clip_segment(np.array([-5.0, -5.0]), np.array([-2.0, -2.0]),
                                                   (0.0, 0.0, 10.0, 10.0)))


class GuardrailMeshTest(unittest.TestCase):
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

    def test_mesh_is_generated_for_mapped_guardrail(self):
        feats = self.features("guard_rail")

        def elev(lat, lon):
            return np.full(np.shape(lat), 12.0, np.float64)

        result = guardrails.mesh(self.frame, lambda _key: feats, (0.0, 0.0, 100.0, 100.0),
                                 elev, (0.0, 0.0, 0.0))
        self.assertIsNotNone(result)
        positions, triangles, uvs = result
        self.assertGreater(len(positions), 0)
        self.assertGreater(len(triangles), 0)
        self.assertEqual(uvs.shape, (len(positions), 2))
        self.assertTrue(np.isfinite(positions).all())
        # With the constant 12 m test terrain, posts rise above the 0.78 m rail top.
        self.assertGreater(float(positions[:, 2].max()), 12.8)
        self.assertGreaterEqual(int(triangles.min()), 0)
        self.assertLess(int(triangles.max()), len(positions))

    def test_mapped_line_is_clipped_contiguously_at_tile_seam(self):
        feats = self.features("guard_rail", xy=[[10.0, 50.0], [190.0, 50.0]])
        elev = lambda lat, lon: np.full(np.shape(lat), 12.0, np.float64)

        left = guardrails.mesh(self.frame, lambda _key: feats, (0.0, 0.0, 100.0, 100.0),
                               elev, (0.0, 0.0, 0.0))
        right = guardrails.mesh(self.frame, lambda _key: feats, (100.0, 0.0, 200.0, 100.0),
                                elev, (100.0, 0.0, 0.0))
        self.assertIsNotNone(left)
        self.assertIsNotNone(right)
        # This horizontal line's rail sections meet at x=100 with no gap or overhang.
        self.assertAlmostEqual(float(left[0][:, 0].max()), 100.0, places=4)
        self.assertAlmostEqual(float(right[0][:, 0].min()), 0.0, places=4)

    def test_unrelated_barrier_produces_no_mesh(self):
        feats = self.features("fence", {"barrier": "fence"})
        result = guardrails.mesh(self.frame, lambda _key: feats, (0.0, 0.0, 100.0, 100.0),
                                 lambda lat, lon: np.full(np.shape(lat), 12.0),
                                 (0.0, 0.0, 0.0))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
