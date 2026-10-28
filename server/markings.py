"""Road markings by country (the user, 2026-10-28: "revamp the road markings system ... as accurate and detailed as
possible ... official government resources on how they mark lanes"): line colours, widths and dash patterns from the
national manuals, summed up in docs/research/2026-10-28-road-markings-standards.md (MUTCD 11th ed., TSRGD / Traffic Signs
Manual ch. 5, RMS, IISR, MLIT, AS 1742.2), and the broken lines cut into real dashes (earthfast.dash_pieces) instead of
the stock dashed decal (3 m of paint and 9 m of a grey smear, its cycle restarting at every tile's edge).

style(country, lon) -> a Style; roads.extras draws the lines with it. Pure.
"""
from collections import namedtuple

import numpy as np

import fast

# colour: the centre line's material; widths in m; patterns (dash, gap) in m. centre_rural / centre_urban: the centre
# line of a two-way road (None: solid); lane_* the lines between lanes of one direction; edge: the edge line's width;
# left_edge: the material of a one-way carriageway's left edge (the Americas' yellow) or None (white like the right);
# double_centre_urban: a town's two-way road gets a double solid centre line (the US practice on arterials) instead of
# a broken one; double_gap: the space between the two lines of a double line.
Style = namedtuple("Style", "centre w w_motorway edge edge_motorway centre_rural centre_urban lane_rural lane_urban "
                            "lane_motorway left_edge double_centre_urban double_gap min_centre_m")

_US = Style(centre="line_yellow", w=0.12, w_motorway=0.15, edge=0.12, edge_motorway=0.15,
            centre_rural=(3.05, 9.14), centre_urban=(3.05, 9.14), lane_rural=(3.05, 9.14), lane_urban=(3.05, 9.14),
            lane_motorway=(3.05, 9.14), left_edge="line_yellow", double_centre_urban=True, double_gap=0.12,
            min_centre_m=4.9)
_CA = _US._replace(centre_rural=(3.0, 6.0), centre_urban=(3.0, 6.0), lane_rural=(3.0, 6.0), lane_urban=(3.0, 6.0),
                   lane_motorway=(3.0, 6.0))
_GB = Style(centre="line_white", w=0.10, w_motorway=0.15, edge=0.15, edge_motorway=0.20,
            centre_rural=(3.0, 6.0), centre_urban=(2.0, 4.0), lane_rural=(2.0, 7.0), lane_urban=(1.0, 5.0),
            lane_motorway=(2.0, 7.0), left_edge=None, double_centre_urban=False, double_gap=0.175, min_centre_m=5.5)
_DE = Style(centre="line_white", w=0.12, w_motorway=0.15, edge=0.12, edge_motorway=0.30,
            centre_rural=(6.0, 12.0), centre_urban=(3.0, 6.0), lane_rural=(6.0, 12.0), lane_urban=(3.0, 6.0),
            lane_motorway=(6.0, 12.0), left_edge=None, double_centre_urban=False, double_gap=0.12, min_centre_m=5.5)
_FR = Style(centre="line_white", w=0.12, w_motorway=0.15, edge=0.15, edge_motorway=0.225,
            centre_rural=(3.0, 10.0), centre_urban=(1.5, 5.0), lane_rural=(3.0, 10.0), lane_urban=(1.5, 5.0),
            lane_motorway=(3.0, 10.0), left_edge=None, double_centre_urban=False, double_gap=0.12, min_centre_m=5.5)
_NL = _DE._replace(centre_rural=(3.0, 9.0), lane_rural=(3.0, 9.0), lane_motorway=(3.0, 9.0), edge_motorway=0.20)
_JP = Style(centre="line_white", w=0.15, w_motorway=0.15, edge=0.15, edge_motorway=0.20,
            centre_rural=(5.0, 5.0), centre_urban=(5.0, 5.0), lane_rural=(6.0, 9.0), lane_urban=(5.0, 5.0),
            lane_motorway=(8.0, 12.0), left_edge=None, double_centre_urban=False, double_gap=0.12, min_centre_m=5.5)
_AU = Style(centre="line_white", w=0.10, w_motorway=0.15, edge=0.12, edge_motorway=0.15,
            centre_rural=(3.0, 9.0), centre_urban=(3.0, 9.0), lane_rural=(3.0, 9.0), lane_urban=(3.0, 9.0),
            lane_motorway=(3.0, 9.0), left_edge=None, double_centre_urban=False, double_gap=0.12, min_centre_m=5.5)
# the manuals' fallback: white centre lines 3 / 6 in towns and 6 / 12 out of them (or yellow where the country paints
# them yellow), lanes 3 / 9
_WHITE = Style(centre="line_white", w=0.12, w_motorway=0.15, edge=0.15, edge_motorway=0.20,
               centre_rural=(6.0, 12.0), centre_urban=(3.0, 6.0), lane_rural=(3.0, 9.0), lane_urban=(3.0, 9.0),
               lane_motorway=(3.0, 9.0), left_edge=None, double_centre_urban=False, double_gap=0.12, min_centre_m=5.5)
_YELLOW = _WHITE._replace(centre="line_yellow")

STYLES = {"US": _US, "CA": _CA, "GB": _GB, "IE": _GB, "IM": _GB, "JE": _GB, "GG": _GB, "DE": _DE, "AT": _DE,
          "CH": _DE, "LI": _DE, "LU": _DE, "FR": _FR, "MC": _FR, "BE": _FR, "NL": _NL, "JP": _JP, "AU": _AU, "NZ": _AU}
# where centre lines are yellow (outside the US and Canada): the Americas, Norway, Finland, South Korea, China, Taiwan,
# Thailand (research table 4.4)
YELLOW_CENTRE = {"MX", "GT", "BZ", "HN", "SV", "NI", "CR", "PA", "CU", "DO", "HT", "PR", "JM", "BS", "TT", "CO", "VE",
                 "EC", "PE", "BO", "CL", "AR", "UY", "PY", "BR", "GY", "SR", "NO", "FI", "KR", "CN", "TW", "TH"}


def style(country=None, lon=None):
    """The marking Style of a country (ISO 3166-1 alpha-2); unknown: by longitude (the Americas yellow, else white)."""
    c = str(country or "").upper()
    if c in STYLES:
        return STYLES[c]
    if c in YELLOW_CENTRE:
        return _YELLOW
    if not c and lon is not None and -170.0 <= float(lon) <= -30.0:
        return _US
    return _WHITE


def dashes(pts, pattern, phase=0.0):
    """The dashes of a broken line along pts (k, 3): [(m, 3) arrays], pattern (dash, gap) m, its cycle starting phase
    m before pts[0] (earthfast.dash_pieces)."""
    pts = np.ascontiguousarray(np.asarray(pts, np.float64)[:, :3] if np.shape(pts)[1] >= 3 else
                               np.c_[np.asarray(pts, np.float64)[:, :2], np.zeros(len(pts))])
    if len(pts) < 2:
        return []
    fast.require(93, "dash_pieces")
    P, starts = fast._ef.dash_pieces(pts, float(pattern[0]), float(pattern[1]), float(phase))
    return [P[a:b] for a, b in zip(starts[:-1], starts[1:]) if b - a >= 2]


def _count(v):
    try:
        n = int(str(v).split(";")[0].strip())
        return n if 0 <= n <= 12 else None
    except (TypeError, ValueError):
        return None


def _vals(v):
    return [p.strip() for p in str(v).split("|")] if v not in (None, "") else None


def _no_change(a, b):
    """change:lanes values of two neighbouring lanes (a left of b, in their travel direction): is crossing between
    them not allowed? (only_left / not_right on a, only_right / not_left on b, or no)"""
    a, b = (a or "yes"), (b or "yes")
    return a in ("no", "not_right", "only_left") or b in ("no", "not_left", "only_right")


def lane_plan(osm, two_way, drive, lanes, left_hand=False):
    """The lines across a carriageway from OpenStreetMap's lane tags (osm: {key: value} of railtags.LANE_COLUMNS, may
    be empty): [(offset m, left of the line's direction, positive left; role, solid)]. role: "centre" (between the
    directions), "twltl" (a two-way turn lane's edge: solid outside, broken inside), "lane" (between lanes of one
    direction). solid: for "lane" True / False; for "centre" None (broken), "double", "solid_right" / "solid_left" (the
    side that may not pass); for "twltl" the through lanes' side (+1 left, -1 right). Without lane tags: None
    (roads.extras' own count). Pure."""
    osm = {k: v for k, v in (osm or {}).items() if v not in (None, "")}
    if not osm:
        return None
    fwd, bwd, both = _count(osm.get("lanes:forward")), _count(osm.get("lanes:backward")), \
        _count(osm.get("lanes:both_ways")) or 0
    if not two_way:
        n = lanes
        ch = _vals(osm.get("change:lanes"))
        lw = drive / max(n, 1)
        out = []
        for k in range(1, n):
            solid = ch is not None and len(ch) == n and _no_change(ch[k - 1], ch[k])
            # lanes left to right in travel: lane k's left edge at -drive/2 + k * lw from the right? (offsets are left
            # positive: the leftmost lane starts at +drive/2)
            out.append((drive / 2 - k * lw, "lane", solid))
        return out
    if fwd is None and bwd is None:
        if lanes and lanes - both >= 2:
            fwd = bwd = None
            half = (lanes - both) / 2
            fwd, bwd = int(round(half + 0.25)), int(lanes - both - int(round(half + 0.25)))
        else:
            return None
    if fwd is None:
        fwd = max(1, (lanes or 2) - both - bwd)
    if bwd is None:
        bwd = max(1, (lanes or 2) - both - fwd)
    n = fwd + bwd + both
    lw = drive / max(n, 1)
    # right-hand traffic: the backward lanes on the left of the way's direction (offsets positive), then the turn lane,
    # then the forward lanes; left-hand traffic the other way round
    left_n, right_n = (bwd, fwd) if not left_hand else (fwd, bwd)
    out = []
    edge = drive / 2
    ch_l = _vals(osm.get("change:lanes:backward" if not left_hand else "change:lanes:forward"))
    ch_r = _vals(osm.get("change:lanes:forward" if not left_hand else "change:lanes:backward"))
    # the left-side lanes (travel against the way in right-hand traffic: their left to right is the way's right to left)
    for k in range(1, left_n):
        off = edge - k * lw
        solid = ch_l is not None and len(ch_l) == left_n and _no_change(ch_l[left_n - k - 1], ch_l[left_n - k])
        out.append((off, "lane", solid))
    c_left = edge - left_n * lw
    no_pass = osm.get("overtaking") == "no"
    pass_f = osm.get("overtaking:forward", osm.get("overtaking"))
    pass_b = osm.get("overtaking:backward", osm.get("overtaking"))
    if both:
        out.append((c_left, "twltl", 1))                # (how: the through lanes' side, +1 left)
        out.append((c_left - both * lw, "twltl", -1))
    else:
        # the centre: solid where nobody may pass, solid on one side where one direction may not (the forward side is
        # the right of the line in right-hand traffic)
        out.append((c_left, "centre", ("double" if no_pass or (pass_f == "no" and pass_b == "no") else
                                       "solid_right" if pass_f == "no" else "solid_left" if pass_b == "no" else
                                       None)))
    c_right = c_left - both * lw
    for k in range(1, right_n):
        off = c_right - k * lw
        solid = ch_r is not None and len(ch_r) == right_n and _no_change(ch_r[k - 1], ch_r[k])
        out.append((off, "lane", solid))
    return out
