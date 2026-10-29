//! Saved areas (the user, 2026-10-28: "add the option to select an area and ... download all data"): the tiles an
//! area's download plan walks, its size, and which cache keys lie in a saved area (pinned: never evicted or cleared).
//! server/areas.py is the glue.

use pyo3::prelude::*;

use crate::cachemap::{tail_tile, tile_of};

const EARTH_R_KM: f64 = 6371.0088;
const MAX_TILES: u64 = 4_000_000;

/// The tiles (x, y) of zoom z under the box (degrees south, west, north, east), with `ring` more tiles round it, in
/// rows from the north-west; an error past MAX_TILES. Never wraps the antimeridian (an area does not cross it).
#[pyfunction]
pub fn area_tiles(s: f64, w: f64, n: f64, e: f64, z: u8, ring: u32) -> PyResult<Vec<(u32, u32)>> {
    if z > 24 || !(s.is_finite() && w.is_finite() && n.is_finite() && e.is_finite()) || n < s || e < w {
        return Err(pyo3::exceptions::PyValueError::new_err("area_tiles: bad box"));
    }
    let last = ((1u64 << z) - 1) as i64;
    let (xa, ya) = tile_of(n, w, z);
    let (xb, yb) = tile_of(s, e, z);
    let r = ring as i64;
    let (x0, x1) = ((xa as i64 - r).max(0), (xb as i64 + r).min(last));
    let (y0, y1) = ((ya as i64 - r).max(0), (yb as i64 + r).min(last));
    if ((x1 - x0 + 1) as u64) * ((y1 - y0 + 1) as u64) > MAX_TILES {
        return Err(pyo3::exceptions::PyValueError::new_err("area_tiles: too many tiles"));
    }
    let mut out = Vec::with_capacity(((x1 - x0 + 1) * (y1 - y0 + 1)) as usize);
    for y in y0..=y1 {
        for x in x0..=x1 {
            out.push((x as u32, y as u32));
        }
    }
    Ok(out)
}

/// The box's area on the sphere (km2).
#[pyfunction]
pub fn area_km2(s: f64, w: f64, n: f64, e: f64) -> f64 {
    if !(n > s && e > w) {
        return 0.0;
    }
    EARTH_R_KM * EARTH_R_KM * (e - w).to_radians() * (n.to_radians().sin() - s.to_radians().sin())
}

/// Normalised Web-Mercator x (0..1 east from -180) and y (0..1 south from the top) of lat / lon.
fn norm(lat: f64, lon: f64) -> (f64, f64) {
    let lat = lat.clamp(-85.051_128_779_806_6, 85.051_128_779_806_6).to_radians();
    ((lon + 180.0) / 360.0, (1.0 - lat.tan().asinh() / std::f64::consts::PI) / 2.0)
}

/// For each cache key: does its z/x/y tile ("<prefix>/z/x/y.ext") touch one of the boxes (degrees south, west,
/// north, east)? Keys that are not tiles: false.
#[pyfunction]
pub fn keys_in_boxes(py: Python<'_>, keys: Vec<String>, boxes: Vec<(f64, f64, f64, f64)>) -> Vec<bool> {
    let nb: Vec<(f64, f64, f64, f64)> = boxes.iter().map(|&(s, w, n, e)| {
        let (xa, ya) = norm(n, w);
        let (xb, yb) = norm(s, e);
        (xa, ya, xb, yb)
    }).collect();
    py.allow_threads(|| keys.iter().map(|k| {
        let Some((z, x, y, _)) = tail_tile(k, 0) else { return false };
        let size = 1.0 / (1u64 << z) as f64;
        let (tx0, ty0) = (x as f64 * size, y as f64 * size);
        let (tx1, ty1) = (tx0 + size, ty0 + size);
        nb.iter().any(|&(xa, ya, xb, yb)| tx0 < xb && tx1 > xa && ty0 < yb && ty1 > ya)
    }).collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn km2() {
        // a 1 x 1 degree box at the equator: ~12,364 km2
        let a = area_km2(0.0, 0.0, 1.0, 1.0);
        assert!((a - 12364.0).abs() < 20.0, "{a}");
    }
}
