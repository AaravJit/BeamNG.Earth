"""Tunnels' and bridges' sound spaces (roads.sound_boxes, the mod's ambience.lua makes them SFXSpaces): a box along a
tube from under its floor to over its vault, split where the tube turns; one under a deck down to SOUND_UNDER_M below
its slab; none for a plain road."""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import roadmesh  # noqa: E402
import roads  # noqa: E402


class SoundBoxesTest(unittest.TestCase):
    def rows(self, chunks):
        r = roads.sound_boxes(chunks)
        return [r[i:i + 8] for i in range(0, len(r), 8)]

    def test_tunnel_and_bridge(self):
        tube = {"tunnel": True, "w": 7.0, "tunnel_h": 6.8,
                "n": [[0, 0, 10], [20, 0, 10.2], [40, 0, 10.4], [60, 0, 10.6], [70, 10, 10.7]]}
        deck = {"deck": True, "w": 9.0, "n": [[0, 50, 30], [30, 50, 30], [60, 50, 30]]}
        road = {"w": 5.0, "n": [[0, 0, 0], [10, 0, 0]]}
        got = self.rows([tube, deck, road])
        kinds = [r[0] for r in got]
        self.assertEqual(kinds, [roads.SOUND_TUNNEL, roads.SOUND_TUNNEL, roads.SOUND_BRIDGE])   # (turned: two)
        k, x, y, z, length, width, height, yaw = got[0]
        self.assertAlmostEqual(x, 30.0)
        self.assertAlmostEqual(length, 60.0 + 2 * roads.SOUND_EXTEND_M)
        self.assertAlmostEqual(width, 7.0 + 2 * roadmesh.TUNNEL_SHOULDER)
        self.assertAlmostEqual(z - height / 2, 10.0 - 0.5, places=2)              # under the floor ...
        self.assertAlmostEqual(z + height / 2, 10.6 + 6.8 + 0.5, places=2)        # ... over the vault
        self.assertAlmostEqual(got[1][7], math.atan2(10, 10), places=3)
        k, x, y, z, length, width, height, yaw = got[2]
        self.assertAlmostEqual(z + height / 2, 30.0 - roadmesh.DECK_DEPTH, places=2)
        self.assertAlmostEqual(z - height / 2, 30.0 - roads.SOUND_UNDER_M, places=2)
        self.assertEqual(width, 9.0)

    def test_city(self):
        # a city tile: one space over the tile, centred on its roads' heights
        road = {"w": 5.0, "n": [[0, 0, 10], [10, 0, 14]]}
        k, x, y, z, length, width, height, yaw = roads.sound_boxes([road], (0.0, 0.0, 256.0, 256.0))
        self.assertEqual((k, x, y, z, yaw), (roads.SOUND_CITY, 128.0, 128.0, 12.0, 0.0))
        self.assertEqual((length, width, height), (258.0, 258.0, roads.SOUND_CITY_H))
        self.assertEqual(roads.sound_boxes([], (0.0, 0.0, 256.0, 256.0)), [])     # (no roads: no heights)

    def test_none(self):
        self.assertEqual(roads.sound_boxes([{"w": 5.0, "n": [[0, 0, 0], [10, 0, 0]]}]), [])
        self.assertEqual(roads.sound_boxes([{"tunnel": True, "no_tube": True, "w": 5.0, "n": [[0, 0, 0], [10, 0, 0]]}]), [])


if __name__ == "__main__":
    unittest.main()
