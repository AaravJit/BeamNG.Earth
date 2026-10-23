from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'mod' / 'beamng_earth' / 'levels' / 'beamng_earth' / 'art' / 'shapes' / 'beamngEarth'
OUT.mkdir(parents=True, exist_ok=True)


def choose_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        'C:/Windows/Fonts/arialbd.ttf',
        'C:/Windows/Fonts/calibrib.ttf',
        'C:/Windows/Fonts/segoeuib.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf',
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def make_sign(label: str, bg: str, accent: str, accent2: str, text_color: str = 'black') -> Image.Image:
    size = 1024
    img = Image.new('RGBA', (size, size), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)

    cx = cy = size // 2
    panel_radius = 422  # outer white panel

    def disc(radius: int, fill: str) -> None:
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=fill)

    if label == 'NO LIMIT':
        # StVO sign 282 (end of route speed limits): white panel, thin black ring,
        # five parallel black stripes at 45 deg from upper right to lower left.
        outer_white_rim = 20
        black_rim = 25
        black_radius = panel_radius - outer_white_rim
        face_radius = black_radius - black_rim
        disc(panel_radius, bg)
        disc(black_radius, 'black')
        disc(face_radius, bg)

        half_len = 384
        inv_sqrt2 = 1 / math.sqrt(2)
        px, py = inv_sqrt2, inv_sqrt2
        start_x = cx + half_len * inv_sqrt2
        start_y = cy - half_len * inv_sqrt2
        end_x = cx - half_len * inv_sqrt2
        end_y = cy + half_len * inv_sqrt2
        stripe_spacing = 22
        stripe_width = 12
        for i in range(5):
            off = (i - 2) * stripe_spacing
            ox, oy = px * off, py * off
            draw.line(
                (start_x + ox, start_y + oy, end_x + ox, end_y + oy),
                fill='black',
                width=stripe_width,
            )
        return img

    # Vienna-convention speed limit sign (StVO 274 style):
    # white panel, thin white margin, thick RED ring, white face, black digits.
    white_margin = 14
    red_outer = panel_radius - white_margin
    red_width = 78
    face_radius = red_outer - red_width
    disc(panel_radius, bg)
    disc(red_outer, accent)
    disc(face_radius, bg)

    # Fit the digits to the face. The font grows until the text's bounding-box corners reach 90% of the
    # face radius. 1-2 digit values keep their natural proportions; 3-digit values may be condensed
    # horizontally (down to 78%) so they can stay tall instead of shrinking, as on real signs.
    max_corner = face_radius * 1.00
    min_squeeze = 1.0 if len(label) <= 2 else 0.78
    font_size = 500
    while font_size > 40:
        font = choose_font(font_size)
        l, t, r, b = draw.textbbox((0, 0), label, font=font, anchor='lt')
        w, h = r - l, b - t
        if h / 2 < max_corner:
            squeeze = min(1.0, 2 * math.sqrt(max_corner ** 2 - (h / 2) ** 2) / w)
            if squeeze >= min_squeeze - 1e-9:
                break
        font_size -= 4
    # Render the digits on their own mask, condense if needed, and centre the ink box in the face.
    mask = Image.new('L', (r + 4, b + 4), 0)
    ImageDraw.Draw(mask).text((0, 0), label, font=font, fill=255, anchor='lt')
    mask = mask.crop((l, t, r, b)).resize((max(1, round(w * squeeze)), h), Image.LANCZOS)
    img.paste(text_color, (round(cx - mask.width / 2), round(cy - mask.height / 2)), mask)
    return img


SIGN_HEIGHT = 2.47          # total model height in metres (matches the value in the Lua furniture table)
PLATE_SIZE = 0.90           # the PNG square is mapped onto this many metres
PLATE_RADIUS = PLATE_SIZE * 422 / 1024   # visible sign disc radius (panel radius 422 px of 1024 in the PNG)
PLATE_TOP_GAP = 0.08        # pole sticking out above the top of the plate (top bracket + cap live here)
PLATE_Y_FRONT, PLATE_Y_BACK = -0.005, 0.005
POLE_RADIUS = 0.03
POLE_Y = PLATE_Y_BACK + POLE_RADIUS + 0.012      # pole centre, just behind the plate
CAP_RADIUS = 0.0375
CAP_HEIGHT = 0.025
BRACKET_OFFSET = 0.24       # bracket centres above/below the plate centre
BRACKET_HALF_H = 0.02
BRACKET_HALF_W = POLE_RADIUS + 0.004
SEGMENTS = 28
METAL_MATERIAL = 'sign_metal'
METAL_COLOR = (0.62, 0.64, 0.66, 1.0)
TEX_DIR = '/levels/beamng_earth/art/shapes/beamngEarth'


METAL_TILE = 0.5            # metres of sign metal covered by one tile of the metal textures
METAL_TEX_SIZE = 512


def planar_uv(p, n):
    """Planar UVs from the dominant normal axis; one metal tile per METAL_TILE metres."""
    ax, ay, az = abs(n[0]), abs(n[1]), abs(n[2])
    if ay >= ax and ay >= az:
        return (p[0] / METAL_TILE, p[2] / METAL_TILE)
    if ax >= az:
        return (p[1] / METAL_TILE, p[2] / METAL_TILE)
    return (p[0] / METAL_TILE, p[1] / METAL_TILE)


class Mesh:
    def __init__(self):
        self.pos, self.nrm, self.uv = [], [], []
        self.tri = {'plate': [], 'metal': []}

    def vert(self, p, n, uv=(0.0, 0.0)):
        self.pos.extend(p)
        self.nrm.extend(n)
        self.uv.extend(uv)
        return len(self.pos) // 3 - 1

    def _p(self, i):
        return tuple(self.pos[3 * i:3 * i + 3])

    def _n(self, i):
        return tuple(self.nrm[3 * i:3 * i + 3])

    def tri_add(self, kind, a, b, c):
        pa, pb, pc = self._p(a), self._p(b), self._p(c)
        u = tuple(x - y for x, y in zip(pb, pa))
        v = tuple(x - y for x, y in zip(pc, pa))
        g = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        ref = [sum(t) for t in zip(self._n(a), self._n(b), self._n(c))]
        if sum(x * y for x, y in zip(g, ref)) < 0:
            b, c = c, b
        self.tri[kind].extend([a, b, c])

    def quad(self, kind, corners, normal):
        """corners: 4 x (pos, uv); one flat normal."""
        ids = [self.vert(p, normal, uv) for p, uv in corners]
        self.tri_add(kind, ids[0], ids[1], ids[2])
        self.tri_add(kind, ids[0], ids[2], ids[3])

    def disc(self, kind, center, radius, normal, plane):
        """Filled circle. plane='xy' (normal along Z) or 'xz' (normal along Y)."""
        c = self.vert(center, normal, planar_uv(center, normal))
        ring = []
        for i in range(SEGMENTS):
            a = 2 * math.pi * i / SEGMENTS
            ca, sa = math.cos(a) * radius, math.sin(a) * radius
            p = (center[0] + ca, center[1] + sa, center[2]) if plane == 'xy' else (center[0] + ca, center[1], center[2] + sa)
            ring.append(self.vert(p, normal, planar_uv(p, normal)))
        for i in range(SEGMENTS):
            self.tri_add(kind, c, ring[i], ring[(i + 1) % SEGMENTS])

    def cylinder(self, kind, center, radius, a0, a1, axis):
        """Smooth side wall. axis='z': centre=(x,y), spans z a0..a1. axis='y': centre=(x,z), spans y a0..a1."""
        repeat = max(1, round(2 * math.pi * radius / METAL_TILE))   # whole tiles around, so the seam matches
        for i in range(SEGMENTS):
            t0 = 2 * math.pi * i / SEGMENTS
            t1 = 2 * math.pi * (i + 1) / SEGMENTS
            ids = []
            for t, lo_hi in ((t0, a0), (t1, a0), (t1, a1), (t0, a1)):
                c, s = math.cos(t), math.sin(t)
                if axis == 'z':
                    p, n = (center[0] + c * radius, center[1] + s * radius, lo_hi), (c, s, 0.0)
                else:
                    p, n = (center[0] + c * radius, lo_hi, center[1] + s * radius), (c, 0.0, s)
                ids.append(self.vert(p, n, (t / (2 * math.pi) * repeat, lo_hi / METAL_TILE)))
            self.tri_add(kind, ids[0], ids[1], ids[2])
            self.tri_add(kind, ids[0], ids[2], ids[3])

    def box(self, kind, x0, x1, y0, y1, z0, z1):
        faces = [
            ((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), (0, -1, 0)),
            ((x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1), (0, 1, 0)),
            ((x0, y0, z0), (x0, y1, z0), (x0, y1, z1), (x0, y0, z1), (-1, 0, 0)),
            ((x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1), (1, 0, 0)),
            ((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (0, 0, 1)),
            ((x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (0, 0, -1)),
        ]
        for a, b, c, d, n in faces:
            self.quad(kind, [(p, planar_uv(p, n)) for p in (a, b, c, d)], n)


def build_mesh() -> Mesh:
    """Upright sign, Z up. Front (-Y) carries the textured plate; the back is bare metal.
    A round pole with a cap sits behind the plate, held by two brackets."""
    m = Mesh()
    r = PLATE_SIZE / 2
    zc = SIGN_HEIGHT - PLATE_TOP_GAP - PLATE_RADIUS      # plate centre height
    yf, yb = PLATE_Y_FRONT, PLATE_Y_BACK

    # textured front (alpha-tested, so the PNG corners are cut away)
    m.quad('plate', [((-r, yf, zc - r), (0, 0)), ((r, yf, zc - r), (1, 0)),
                     ((r, yf, zc + r), (1, 1)), ((-r, yf, zc + r), (0, 1))], (0, -1, 0))
    # metal back disc + rim, so the back is a solid round plate
    m.disc('metal', (0.0, yb, zc), PLATE_RADIUS, (0, 1, 0), 'xz')
    m.cylinder('metal', (0.0, zc), PLATE_RADIUS, yf, yb, 'y')

    # round pole, closed at both ends, with a wider cap on top
    cap_z0 = SIGN_HEIGHT - CAP_HEIGHT
    m.cylinder('metal', (0.0, POLE_Y), POLE_RADIUS, 0.0, cap_z0, 'z')
    m.disc('metal', (0.0, POLE_Y, 0.0), POLE_RADIUS, (0, 0, -1), 'xy')
    m.cylinder('metal', (0.0, POLE_Y), CAP_RADIUS, cap_z0, SIGN_HEIGHT, 'z')
    m.disc('metal', (0.0, POLE_Y, cap_z0), CAP_RADIUS, (0, 0, -1), 'xy')
    m.disc('metal', (0.0, POLE_Y, SIGN_HEIGHT), CAP_RADIUS, (0, 0, 1), 'xy')

    # two brackets clamping the plate to the pole (top and bottom)
    y_far = POLE_Y + POLE_RADIUS + 0.006
    for dz in (-BRACKET_OFFSET, BRACKET_OFFSET):
        m.box('metal', -BRACKET_HALF_W, BRACKET_HALF_W, yb, y_far, zc + dz - BRACKET_HALF_H, zc + dz + BRACKET_HALF_H)
    return m


def dae_xml(name: str, png_name: str) -> str:
    m = build_mesh()
    n = len(m.pos) // 3

    def fmt(vals):
        return ' '.join(repr(round(float(v), 5)) for v in vals)

    def tri_block(kind, mat):
        ids = m.tri[kind]
        return (f'        <triangles count="{len(ids) // 3}" material="{mat}_material">\n'
                f'          <input semantic="VERTEX" source="#{name}_vertices" offset="0"/>\n'
                f'          <input semantic="NORMAL" source="#{name}_normals" offset="0"/>\n'
                f'          <input semantic="TEXCOORD" source="#{name}_uv" offset="0" set="0"/>\n'
                f'          <p>{" ".join(map(str, ids))}</p>\n'
                f'        </triangles>\n')

    mr, mg, mb, _ = METAL_COLOR
    return f"""<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
  <asset>
    <contributor>
      <author>BeamNG Earth</author>
      <authoring_tool>Python generator</authoring_tool>
    </contributor>
    <created>2026-10-23T00:00:00</created>
    <modified>2026-10-23T00:00:00</modified>
    <unit meter="1" name="meter"/>
    <up_axis>Z_UP</up_axis>
  </asset>
  <library_images>
    <image id="{name}_image">
      <init_from>{png_name}</init_from>
    </image>
  </library_images>
  <library_effects>
    <effect id="{name}_effect">
      <profile_COMMON>
        <newparam sid="{name}_surface">
          <surface type="2D">
            <init_from>{name}_image</init_from>
          </surface>
        </newparam>
        <newparam sid="{name}_sampler">
          <sampler2D>
            <source>{name}_surface</source>
          </sampler2D>
        </newparam>
        <technique sid="common">
          <lambert>
            <diffuse>
              <texture texture="{name}_sampler" texcoord="UVMap"/>
            </diffuse>
          </lambert>
        </technique>
      </profile_COMMON>
    </effect>
    <effect id="{METAL_MATERIAL}_effect">
      <profile_COMMON>
        <technique sid="common">
          <lambert>
            <diffuse>
              <color>{mr} {mg} {mb} 1</color>
            </diffuse>
          </lambert>
        </technique>
      </profile_COMMON>
    </effect>
  </library_effects>
  <library_materials>
    <material id="{name}_material" name="{name}">
      <instance_effect url="#{name}_effect"/>
    </material>
    <material id="{METAL_MATERIAL}_material" name="{METAL_MATERIAL}">
      <instance_effect url="#{METAL_MATERIAL}_effect"/>
    </material>
  </library_materials>
  <library_geometries>
    <geometry id="{name}_geom">
      <mesh>
        <source id="{name}_positions">
          <float_array id="{name}_positions_array" count="{n * 3}">{fmt(m.pos)}</float_array>
          <technique_common>
            <accessor source="#{name}_positions_array" count="{n}" stride="3">
              <param name="X" type="float"/>
              <param name="Y" type="float"/>
              <param name="Z" type="float"/>
            </accessor>
          </technique_common>
        </source>
        <source id="{name}_normals">
          <float_array id="{name}_normals_array" count="{n * 3}">{fmt(m.nrm)}</float_array>
          <technique_common>
            <accessor source="#{name}_normals_array" count="{n}" stride="3">
              <param name="X" type="float"/>
              <param name="Y" type="float"/>
              <param name="Z" type="float"/>
            </accessor>
          </technique_common>
        </source>
        <source id="{name}_uv">
          <float_array id="{name}_uv_array" count="{n * 2}">{fmt(m.uv)}</float_array>
          <technique_common>
            <accessor source="#{name}_uv_array" count="{n}" stride="2">
              <param name="S" type="float"/>
              <param name="T" type="float"/>
            </accessor>
          </technique_common>
        </source>
        <vertices id="{name}_vertices">
          <input semantic="POSITION" source="#{name}_positions"/>
        </vertices>
{tri_block('plate', name)}{tri_block('metal', METAL_MATERIAL)}      </mesh>
    </geometry>
  </library_geometries>
  <library_visual_scenes>
    <visual_scene id="{name}_scene" name="{name}_scene">
      <node id="{name}_node" name="{name}" type="NODE">
        <instance_geometry url="#{name}_geom">
          <bind_material>
            <technique_common>
              <instance_material symbol="{name}_material" target="#{name}_material">
                <bind_vertex_input semantic="UVMap" input_semantic="TEXCOORD" input_set="0"/>
              </instance_material>
              <instance_material symbol="{METAL_MATERIAL}_material" target="#{METAL_MATERIAL}_material"/>
            </technique_common>
          </bind_material>
        </instance_geometry>
      </node>
    </visual_scene>
  </library_visual_scenes>
  <scene>
    <instance_visual_scene url="#{name}_scene"/>
  </scene>
</COLLADA>
"""


def write_metal_textures() -> bool:
    """Procedural galvanized-steel base colour + normal map (tileable). Returns False without numpy."""
    try:
        import numpy as np
    except ImportError:
        print('  numpy not found: skipping metal textures (flat metal colour is used instead)')
        return False

    n = METAL_TEX_SIZE
    rng = np.random.default_rng(7)
    fx = np.fft.fftfreq(n)[None, :]
    fy = np.fft.fftfreq(n)[:, None]

    def field(sx, sy):
        """Tileable gaussian-filtered noise, normalised to unit std (sx/sy in pixels)."""
        spec = np.fft.fft2(rng.standard_normal((n, n))) * np.exp(-2 * np.pi ** 2 * ((sx * fx) ** 2 + (sy * fy) ** 2))
        out = np.real(np.fft.ifft2(spec))
        return (out - out.mean()) / out.std()

    mottling = field(36, 36)     # slow blotchy variation
    streaks = field(0.9, 28)     # fine vertical brushing/rolling marks
    grain = field(0.8, 0.8)      # small speckle
    lum = 1 + 0.050 * mottling + 0.040 * streaks + 0.035 * grain
    base = np.array([0.74, 0.76, 0.79]) * lum[..., None]
    rgb = (np.clip(base, 0, 1) * 255).astype(np.uint8)
    Image.fromarray(rgb, 'RGB').save(OUT / 'sign_metal_basecolor.png')

    height = 0.6 * streaks + 0.4 * grain
    dx = (np.roll(height, -1, axis=1) - np.roll(height, 1, axis=1)) * 0.5
    dy = (np.roll(height, -1, axis=0) - np.roll(height, 1, axis=0)) * 0.5
    nm = np.stack([-dx * 0.8, dy * 0.8, np.ones_like(height)], axis=-1)
    nm /= np.linalg.norm(nm, axis=-1, keepdims=True)
    Image.fromarray(((nm * 0.5 + 0.5) * 255).astype(np.uint8), 'RGB').save(OUT / 'sign_metal_normal.png')
    print('  sign_metal_basecolor.png, sign_metal_normal.png')
    return True


def write_materials(has_metal_textures: bool) -> None:
    """sign_speed.materials.json covering every sign PNG in the folder, plus the shared metal material."""
    if has_metal_textures:
        metal = {
            'name': METAL_MATERIAL,
            'mapTo': METAL_MATERIAL,
            'class': 'Material',
            'version': 1.5,
            'Stages': [{
                'baseColorMap': f'{TEX_DIR}/sign_metal_basecolor.png',
                'normalMap': f'{TEX_DIR}/sign_metal_normal.png',
                'baseColorFactor': [1.0, 1.0, 1.0, 1.0],
                'metallicFactor': 1.0,
                'roughnessFactor': 0.42,
                'normalMapStrength': 0.7,
                'useAnisotropic': True,
            }, {}, {}, {}],
            'doubleSided': False,
        }
    else:
        metal = {
            'name': METAL_MATERIAL,
            'mapTo': METAL_MATERIAL,
            'class': 'Material',
            'Stages': [{'diffuseColor': list(METAL_COLOR), 'specular': [0.75, 0.77, 0.8, 1.0],
                        'specularPower': 48, 'pixelSpecular': True}, {}, {}, {}],
            'doubleSided': False,
        }
    mats = {METAL_MATERIAL: metal}
    for png in sorted(OUT.glob('sign_speed*.png')):
        n = png.stem
        mats[n] = {
            'name': n,
            'mapTo': n,
            'class': 'Material',
            'Stages': [{'colorMap': f'{TEX_DIR}/{png.name}', 'diffuseColor': [1, 1, 1, 1]}, {}, {}, {}],
            'alphaTest': True,
            'alphaRef': 127,
            'doubleSided': False,
        }
    (OUT / 'sign_speed.materials.json').write_text(json.dumps(mats, indent=2), encoding='utf-8')


def parse_speed(value: str) -> int | str:
    text = value.strip().lower()
    if text in {'none', 'no-limit', 'nolimit', 'infinite', 'inf'}:
        return 'none'
    try:
        speed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("speed must be an integer from 5 to 200 or 'none'") from exc
    if not 5 <= speed <= 200:
        raise argparse.ArgumentTypeError("speed must be between 5 and 200 inclusive")
    return speed


def build_sign_specs(speeds: Iterable[int | str] | None = None) -> dict[str, tuple[str, str, str, str, str]]:
    defaults = [40, 50, 60, 80, 100, 120, 130, 150, 'none']
    requested = list(speeds) if speeds is not None else defaults
    sign_specs: dict[str, tuple[str, str, str, str, str]] = {}
    for speed in requested:
        if speed == 'none':
            sign_specs['sign_speed_none'] = ('NO LIMIT', '#F7F7F7', 'black', 'black', 'black')
        else:
            sign_specs[f'sign_speed{int(speed)}kph'] = (str(int(speed)), '#F7F7F7', '#CC1B1B', '#CC1B1B', 'black')
    return sign_specs


def write_signs(speeds: Iterable[int | str] | None = None) -> None:
    sign_specs = build_sign_specs(speeds)
    for name, (label, bg, accent, accent2, text_color) in sign_specs.items():
        img = make_sign(label, bg, accent, accent2, text_color)
        png = OUT / f'{name}.png'
        img.save(png)
        dae = OUT / f'{name}.dae'
        dae.write_text(dae_xml(name, png.name), encoding='utf-8')
        print(f'  {png.name} ({png.stat().st_size} bytes)')
    write_materials(write_metal_textures())
    print('  sign_speed.materials.json')


def main() -> None:
    parser = argparse.ArgumentParser(description='Generate European speed-limit sign artwork.')
    parser.add_argument(
        '--speed',
        type=parse_speed,
        help="Generate a sign for one speed between 5 and 200, or 'none' for the no-limit sign. Defaults to the full set.",
    )
    args = parser.parse_args()

    if args.speed is None:
        write_signs()
    else:
        write_signs([args.speed])

    print(f'Created European speed sign assets in {OUT}')


if __name__ == '__main__':
    main()