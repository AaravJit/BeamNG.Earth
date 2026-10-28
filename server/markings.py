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
