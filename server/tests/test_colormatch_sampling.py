"""Sparse sRGB lookup during seam inpainting preserves the previous dense conversion exactly."""
from pathlib import Path
import sys
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import colormatch as cm  # noqa: E402


def dense_bilinear(img, y, x):
    """The original sampler, independent of the optimized lookup path."""
    h, w = img.shape[:2]
    y, x = np.clip(y, 0, h - 1.001), np.clip(x, 0, w - 1.001)
    y0, x0 = np.floor(y).astype(np.int64), np.floor(x).astype(np.int64)
    fy, fx = (y - y0)[..., None], (x - x0)[..., None]
    return ((img[y0, x0] * (1 - fx) + img[y0, x0 + 1] * fx) * (1 - fy)
            + (img[y0 + 1, x0] * (1 - fx) + img[y0 + 1, x0 + 1] * fx) * fy)


class DenseField(cm.Field):
    def _inpaint(self, x_lin, rgb, strip, F, margin):
        """Previous inpaint path: convert the whole source before sampling it."""
        py, px, d16, s = strip
        ny, nx = self.line[s, 2], self.line[s, 3]
        r = F / cm.LINE_F
        ya, xa = py + (cm.REACH - d16) * r * ny, px + (cm.REACH - d16) * r * nx
        yb, xb = py - (cm.REACH + d16) * r * ny, px - (cm.REACH + d16) * r * nx
        lo = min(ya.min(), yb.min(), xa.min(), xb.min())
        hi = max(ya.max(), yb.max(), xa.max(), xb.max())
        if lo < 0 or hi > min(rgb.shape[:2]) - 1.001:
            src = margin() if margin is not None else None
            if src is None:
                src = np.pad(rgb, ((cm.MARGIN, cm.MARGIN), (cm.MARGIN, cm.MARGIN), (0, 0)), mode="edge")
            src, o = cm.SRGB2LIN[src], cm.MARGIN
        else:
            src, o = cm.SRGB2LIN[rgb], 0
        pa = dense_bilinear(src, ya + o, xa + o) * self.gA[s]
        pb = dense_bilinear(src, yb + o, xb + o) * self.gB[s]
        t = ((d16 + cm.REACH) / (2 * cm.REACH))[:, None]
        x_lin[py, px] = (1 - t) * pb + t * pa


def fields(z=16, edge=False, normal=(0.0, 1.0), band=True, noop=False):
    """Small real tile-sized fields, with an interior seam or one touching neighbouring tiles."""
    F = 2. ** (z - cm.GRID_Z)
    n = int(cm.TILE / F)
    rng = np.random.default_rng(3027)
    C = rng.uniform(-0.2, 0.2, (n // cm.COARSE + 2 * cm.PADC,) * 2 + (3,)).astype(np.float32)
    if noop:
        C[:] = 0
    elif not band:
        C[:, C.shape[1] // 2:] = 0               # some pixels must retain their original bytes
    args = [n, C]
    if band:
        pb = cm.PADC * cm.COARSE
        ys, xs = np.mgrid[-pb:n + pb, -pb:n + pb]
        qy, qx = n / 2, 0.4 if edge else n / 2 + 0.2
        ny, nx = normal
        mask = np.abs((ys + 0.5 - qy) * ny + (xs + 0.5 - qx) * nx) <= cm.BAND * cm.COARSE
        if not edge:
            mask &= (ys >= 8) & (ys < n - 8) & (xs >= 8) & (xs < n - 8)
        sid = np.full(ys.shape, -1, np.int32)
        sid[mask] = 0
        args += [sid, np.array([[qy, qx, ny, nx]], np.float32),
                 np.array([[.1, .2, .05]], np.float32), np.array([[-.1, -.2, -.05]], np.float32)]
    rgb = rng.integers(0, 256, (256, 256, 3), dtype=np.uint8)
    canvas = rng.integers(0, 256, (256 + 2 * cm.MARGIN,) * 2 + (3,), dtype=np.uint8)
    canvas[cm.MARGIN:-cm.MARGIN, cm.MARGIN:-cm.MARGIN] = rgb
    return cm.Field(*args), DenseField(*args), rgb, canvas


class SparseSamplingTest(unittest.TestCase):
    def test_lookup_before_interpolation_matches_dense_for_all_srgb_values(self):
        img = np.arange(4 * 256 * 3, dtype=np.uint16).astype(np.uint8).reshape(4, 256, 3)
        # Broadcast fractional coordinates, exact centres, and both clamped edges.
        for offset in (0.0, 0.37, -100.5, 100.5):
            y = np.array([-10.0, 0.0, .25, 1.8, 3.0, 10.0])[:, None]
            x = np.arange(256)[None, :] + offset
            with self.subTest(offset=offset):
                expected = dense_bilinear(cm.SRGB2LIN[img], y, x)
                np.testing.assert_array_equal(cm.bilinear(img, y, x, lut=cm.SRGB2LIN), expected)
                np.testing.assert_array_equal(cm.bilinear(cm.SRGB2LIN[img], y, x), expected)

    def test_band_tiles_and_margin_paths_are_byte_identical(self):
        for z in (15, 16):
            cases = ((False, (0., 1.), "provided"), (False, (1., 0.), "provided"),
                     (False, (.6, -.8), "provided"), (True, (0., 1.), "provided"),
                     (True, (0., 1.), "absent"), (True, (0., 1.), "unavailable"))
            for edge, normal, kind in cases:
                with self.subTest(z=z, edge=edge, normal=normal, margin=kind):
                    field, dense, rgb, canvas = fields(z, edge, normal)
                    before = rgb.copy()
                    margin = None if kind == "absent" else mock.Mock(return_value=canvas if kind == "provided" else None)
                    expected = dense.apply(rgb, z, 0, 0, z, margin)
                    if margin is not None:
                        calls = margin.call_count
                        margin.reset_mock()
                    actual = field.apply(rgb, z, 0, 0, z, margin)
                    self.assertIsNotNone(actual)
                    np.testing.assert_array_equal(actual, expected)
                    np.testing.assert_array_equal(rgb, before)
                    if margin is not None:
                        self.assertEqual(margin.call_count, calls)
                        self.assertEqual(calls, int(edge))

    def test_smooth_and_unchanged_tiles_keep_their_bytes(self):
        for noop in (False, True):
            with self.subTest(noop=noop):
                field, dense, rgb, _ = fields(band=False, noop=noop)
                margin = mock.Mock(side_effect=AssertionError("no seam to inpaint"))
                expected = dense.apply(rgb, 16, 0, 0, 16, margin)
                actual = field.apply(rgb, 16, 0, 0, 16, margin)
                if noop:
                    self.assertIsNone(actual)
                    self.assertIsNone(expected)
                else:
                    np.testing.assert_array_equal(actual, expected)
                    np.testing.assert_array_equal(actual[:, 192:], rgb[:, 192:])
                margin.assert_not_called()

    def test_black_and_white_saturation_match_dense_inpaint(self):
        for value in (0, 1, 10, 11, 254, 255):
            with self.subTest(value=value):
                field, dense, rgb, canvas = fields(edge=True)
                rgb[:] = value
                canvas[:] = value
                expected = dense.apply(rgb, 16, 0, 0, 16, lambda: canvas)
                np.testing.assert_array_equal(field.apply(rgb, 16, 0, 0, 16, lambda: canvas), expected)


if __name__ == "__main__":
    unittest.main()
