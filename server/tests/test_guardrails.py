"""Mapped guardrail mesh geometry and Overture tag filtering."""
import json
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import guardrails


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


if __name__ == "__main__":
    unittest.main()
