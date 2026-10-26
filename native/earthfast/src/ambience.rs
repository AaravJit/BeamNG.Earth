//! The forest ambience of a vegetation tile (2026-10-26, the user: "Start with 1"): the stock maps' wind in the
//! trees and room tone (lua/ge/extensions/core/audioForest.lua) fed from our trees instead of a level's Forest. The
//! stock module thins a level's Forest items into nodes ~20 m apart (a node: its ground z, half its tree's height,
//! the mean size class of the items it took, how many); here a tile's items are binned into squares of the anchor
//! frame, which gives the same kind of node without the greedy search. The ambience set (wind and room tone) is
//! the stock maps' by climate: temperate (ConiferousDeciduous), Mediterranean, desert, tropical.

use numpy::{IntoPyArray, PyArray2, PyReadonlyArray1};
use pyo3::prelude::*;
use std::collections::BTreeMap;

pub const TEMPERATE: u8 = 0;
pub const MEDITERRANEAN: u8 = 1;
pub const DESERT: u8 = 2;
pub const TROPICAL: u8 = 3;

/// The sound nodes of a tile's items (group, x, y in the anchor frame, ground z, height m): the items whose group is
/// in `sound` binned into cell x cell squares (from the frame's origin); per square with any, a row [x, y (the
/// items' centroid), z (their mean ground), depth (half the tallest one's height: the stock nodes' lift), size (the
/// mean class: 0 below h_mid, 0.5 below h_large, else 1), count], in (row, column) order of the squares.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn audio_nodes<'py>(py: Python<'py>, group: PyReadonlyArray1<'py, u8>, x: PyReadonlyArray1<'py, f64>,
                        y: PyReadonlyArray1<'py, f64>, z: PyReadonlyArray1<'py, f64>,
                        height: PyReadonlyArray1<'py, f64>, sound: Vec<u8>, cell: f64, h_mid: f64, h_large: f64)
                        -> Bound<'py, PyArray2<f64>> {
    let (group, x, y, z, height) = (group.as_array(), x.as_array(), y.as_array(), z.as_array(), height.as_array());
    let mut take = [false; 256];
    for g in sound {
        take[g as usize] = true;
    }
    // (count, sum x, sum y, sum z, tallest, sum class)
    let mut cells: BTreeMap<(i64, i64), (f64, f64, f64, f64, f64, f64)> = BTreeMap::new();
    for i in 0..group.len() {
        if !take[group[i] as usize] {
            continue;
        }
        let h = height[i];
        let class = if h < h_mid { 0.0 } else if h < h_large { 0.5 } else { 1.0 };
        let key = ((y[i] / cell).floor() as i64, (x[i] / cell).floor() as i64);
        let a = cells.entry(key).or_insert((0.0, 0.0, 0.0, 0.0, 0.0, 0.0));
        a.0 += 1.0;
        a.1 += x[i];
        a.2 += y[i];
        a.3 += z[i];
        a.4 = a.4.max(h);
        a.5 += class;
    }
    let mut out = Vec::with_capacity(cells.len() * 6);
    for (n, sx, sy, sz, top, sc) in cells.into_values() {
        out.extend_from_slice(&[sx / n, sy / n, sz / n, 0.5 * top, sc / n, n]);
    }
    let k = out.len() / 6;
    numpy::ndarray::Array2::from_shape_vec((k, 6), out).unwrap().into_pyarray(py)
}

/// The ambience set of a tile at (lat, lon) with these items' groups: (set, variant), the set TEMPERATE,
/// MEDITERRANEAN, DESERT or TROPICAL, the variant the stock _01 (0) or _02 (1). ids: the groups (desert_shrub,
/// desert_rock, juniper). A coarse climate as vegetation.regional's (until a climate map): desert where most items
/// are the dry West's (the stock Utah's _01; west of 114 W, the Mojave's, Johnson Valley's _02), tropical within the
/// tropics, Mediterranean round the Mediterranean Sea, temperate elsewhere (the US West Coast's as West Coast USA's
/// _02, else East Coast USA's _01).
#[pyfunction]
pub fn audio_biome(group: PyReadonlyArray1<'_, u8>, lat: f64, lon: f64, ids: [u8; 3]) -> (u8, u8) {
    let group = group.as_array();
    let dry = group.iter().filter(|g| ids.contains(g)).count();
    biome(group.len(), dry, lat, lon)
}

fn biome(total: usize, dry: usize, lat: f64, lon: f64) -> (u8, u8) {
    if total > 0 && 2 * dry >= total {
        return (DESERT, (lon < -114.0) as u8);
    }
    if lat.abs() < 23.5 {
        return (TROPICAL, 0);
    }
    if lat > 30.0 && lat < 46.0 && lon > -10.0 && lon < 40.0 {
        return (MEDITERRANEAN, 0);
    }
    let west_coast = lon < -114.0 && lat > 32.0 && lat < 49.5;
    (TEMPERATE, west_coast as u8)
}

/// The sound spaces along lines (the stock maps' SFXSpace boxes: a tunnel's reverb in its tube, the one under a bridge
/// below its deck): lines given as points (n, 3) with offsets (m + 1); per line its kind (returned as is), width, and
/// the box's reach below its lowest and above its highest point (negative: under it). Consecutive segments go into one
/// box while it stays within max_len, its heading within max_turn (radians) of its first segment's and its heights
/// within max_dz; each box reaches `extend` past its ends (boxes overlap at their joints). Rows [kind, x, y, z (the
/// box's centre), length, width, height, yaw (radians, its length along (cos, sin))].
#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn sound_boxes<'py>(py: Python<'py>, pts: numpy::PyReadonlyArray2<'py, f64>, off: PyReadonlyArray1<'py, i64>,
                        kind: PyReadonlyArray1<'py, f64>, width: PyReadonlyArray1<'py, f64>,
                        below: PyReadonlyArray1<'py, f64>, above: PyReadonlyArray1<'py, f64>, max_len: f64,
                        max_turn: f64, max_dz: f64, extend: f64) -> Bound<'py, PyArray2<f64>> {
    let (pts, off, kind, width, below, above) =
        (pts.as_array(), off.as_array(), kind.as_array(), width.as_array(), below.as_array(), above.as_array());
    let mut out: Vec<f64> = Vec::new();
    for l in 0..kind.len() {
        let (a, b) = (off[l] as usize, off[l + 1] as usize);
        let mut i = a;
        while i + 1 < b {
            // a run of segments from point i to point j
            let (x0, y0) = (pts[[i, 0]], pts[[i, 1]]);
            let h0 = (pts[[i + 1, 1]] - y0).atan2(pts[[i + 1, 0]] - x0);
            let (mut zlo, mut zhi) = (pts[[i, 2]].min(pts[[i + 1, 2]]), pts[[i, 2]].max(pts[[i + 1, 2]]));
            let mut j = i + 1;
            while j + 1 < b {
                let (dx, dy) = (pts[[j + 1, 0]] - pts[[j, 0]], pts[[j + 1, 1]] - pts[[j, 1]]);
                let mut turn = dy.atan2(dx) - h0;
                while turn > std::f64::consts::PI { turn -= 2.0 * std::f64::consts::PI; }
                while turn < -std::f64::consts::PI { turn += 2.0 * std::f64::consts::PI; }
                let z = pts[[j + 1, 2]];
                let len = (pts[[j + 1, 0]] - x0).hypot(pts[[j + 1, 1]] - y0);
                if turn.abs() > max_turn || len > max_len || zhi.max(z) - zlo.min(z) > max_dz {
                    break;
                }
                zlo = zlo.min(z);
                zhi = zhi.max(z);
                j += 1;
            }
            let (x1, y1) = (pts[[j, 0]], pts[[j, 1]]);
            let len = (x1 - x0).hypot(y1 - y0);
            if len > 1e-6 {
                let (z0, z1) = (zlo - below[l], zhi + above[l]);
                out.extend_from_slice(&[kind[l], (x0 + x1) / 2.0, (y0 + y1) / 2.0, (z0 + z1) / 2.0, len + 2.0 * extend,
                                        width[l], z1 - z0, (y1 - y0).atan2(x1 - x0)]);
            }
            i = j;
        }
    }
    let k = out.len() / 8;
    numpy::ndarray::Array2::from_shape_vec((k, 8), out).unwrap().into_pyarray(py)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn biomes() {
        assert_eq!(biome(10, 8, 38.6, -109.5), (DESERT, 0));        // Moab
        assert_eq!(biome(10, 8, 34.4, -116.5), (DESERT, 1));        // Johnson Valley
        assert_eq!(biome(10, 1, 39.3, -76.6), (TEMPERATE, 0));      // Baltimore
        assert_eq!(biome(10, 1, 37.8, -122.3), (TEMPERATE, 1));     // Oakland
        assert_eq!(biome(0, 0, 41.9, 12.5), (MEDITERRANEAN, 0));    // Rome, no items
        assert_eq!(biome(4, 0, -3.1, -60.0), (TROPICAL, 0));        // Manaus
    }
}
