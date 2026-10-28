//! roads.TunnelProfiles: a covered tunnel section (a low arch or a full tube) carried on along the tunnel until a ground
//! road crossing over it is covered, its carriageway and a clearance past it (GitHub #450, an underpass under the A-7 near
//! Málaga: on lidar each carriageway's embankment was its own short arch, the first 8 m long under a 13 m carriageway,
//! and its mouths and terrain holes stood in the motorway's lanes: cars fell through).

use numpy::{IntoPyArray, PyArray1, PyReadonlyArray1, PyReadonlyArray2};
use pyo3::prelude::*;

/// Arc positions along polyline a (with arc lengths sa) where it crosses polyline b, and the sine of the angle there.
fn hits(a: &[[f64; 2]], sa: &[f64], b: &[[f64; 2]]) -> Vec<(f64, f64)> {
    let mut out = Vec::new();
    for i in 0..a.len().saturating_sub(1) {
        let (p, r) = (a[i], [a[i + 1][0] - a[i][0], a[i + 1][1] - a[i][1]]);
        let rl = (r[0] * r[0] + r[1] * r[1]).sqrt().max(1e-12);
        for j in 0..b.len().saturating_sub(1) {
            let (q, t) = (b[j], [b[j + 1][0] - b[j][0], b[j + 1][1] - b[j][1]]);
            let den = r[0] * t[1] - r[1] * t[0];
            if den.abs() < 1e-12 {
                continue;
            }
            let u = ((q[0] - p[0]) * t[1] - (q[1] - p[1]) * t[0]) / den;
            let v = ((q[0] - p[0]) * r[1] - (q[1] - p[1]) * r[0]) / den;
            if (0.0..=1.0).contains(&u) && (0.0..=1.0).contains(&v) {
                let tl = (t[0] * t[0] + t[1] * t[1]).sqrt().max(1e-12);
                let sin = (den / (rl * tl)).abs();
                out.push((sa[i] + u * (sa[i + 1] - sa[i]), sin));
            }
        }
    }
    out
}

/// kinds (per sample of the tunnel line xy at arc lengths s: 0 open, 1 low arch, 2 full) with each covered run grown
/// to cover every ground road (lines pts[starts[k]..starts[k + 1]], half widths halves[k]) crossing the tunnel within
/// reach of the run: along the tunnel (half width + clear) / max(sin of the crossing angle, min_sin) either side of
/// the crossing. Then open gaps shorter than min_gap between covered runs take the lower kind beside them. Pure.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn tunnel_cover_roads<'py>(
    py: Python<'py>,
    xy: PyReadonlyArray2<'py, f64>,
    s: PyReadonlyArray1<'py, f64>,
    kinds: PyReadonlyArray1<'py, i64>,
    pts: PyReadonlyArray2<'py, f64>,
    starts: PyReadonlyArray1<'py, i64>,
    halves: PyReadonlyArray1<'py, f64>,
    clear: f64,
    min_sin: f64,
    reach: f64,
    min_gap: f64,
) -> PyResult<Bound<'py, PyArray1<i64>>> {
    let xa = xy.as_array();
    let a: Vec<[f64; 2]> = (0..xa.nrows()).map(|i| [xa[[i, 0]], xa[[i, 1]]]).collect();
    let s: Vec<f64> = s.as_array().to_vec();
    let mut k: Vec<i64> = kinds.as_array().to_vec();
    let pa = pts.as_array();
    let p: Vec<[f64; 2]> = (0..pa.nrows()).map(|i| [pa[[i, 0]], pa[[i, 1]]]).collect();
    let st: Vec<usize> = starts.as_array().iter().map(|&v| v.max(0) as usize).collect();
    let hw: Vec<f64> = halves.as_array().to_vec();
    let n = k.len();
    if a.len() != n || s.len() != n || st.len() != hw.len() + 1 {
        return Err(pyo3::exceptions::PyValueError::new_err("tunnel_cover_roads: array sizes differ"));
    }
    // covered runs (inclusive sample ranges) and their kinds
    let mut runs: Vec<(usize, usize, i64)> = Vec::new();
    let mut i = 0;
    while i < n {
        if k[i] == 0 {
            i += 1;
            continue;
        }
        let mut j = i;
        while j + 1 < n && k[j + 1] != 0 {
            j += 1;
        }
        let kind = (i..=j).map(|q| k[q]).min().unwrap_or(1);
        runs.push((i, j, kind));
        i = j + 1;
    }
    let mut crossings = Vec::new();
    for r in 0..hw.len() {
        let (b0, b1) = (st[r], st[r + 1].min(p.len()));
        if b1 < b0 + 2 {
            continue;
        }
        for (sc, sin) in hits(&a, &s, &p[b0..b1]) {
            crossings.push((sc, (hw[r] + clear) / sin.max(min_sin)));
        }
    }
    for &(i0, i1, kind) in &runs {
        let (mut lo, mut hi) = (s[i0], s[i1]);
        for &(sc, ext) in &crossings {
            if sc >= s[i0] - reach && sc <= s[i1] + reach {
                lo = lo.min(sc - ext);
                hi = hi.max(sc + ext);
            }
        }
        // (rounded outward to the samples: the mouth at or past the road, never short of it)
        for q in 0..n {
            let past_lo = s[q] >= lo || (q + 1 < n && s[q + 1] > lo);
            let before_hi = s[q] <= hi || (q > 0 && s[q - 1] < hi);
            if k[q] == 0 && past_lo && before_hi {
                k[q] = kind;
            }
        }
    }
    // short open gaps between covered runs closed
    let mut q = 0;
    while q < n {
        if k[q] != 0 {
            q += 1;
            continue;
        }
        let mut e = q;
        while e + 1 < n && k[e + 1] == 0 {
            e += 1;
        }
        if q > 0 && e + 1 < n && s[e + 1] - s[q - 1] < min_gap {
            let kind = k[q - 1].min(k[e + 1]);
            for v in q..=e {
                k[v] = kind;
            }
        }
        q = e + 1;
    }
    Ok(k.into_pyarray(py))
}
