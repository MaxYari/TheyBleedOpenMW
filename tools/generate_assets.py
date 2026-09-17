#!/usr/bin/env python3
"""Generate blood droplet-cluster textures (DDS) and matching decal quads (NIF 4.0.0.2).

Stdlib only. Run from anywhere:  python3 tools/generate_assets.py [--seed N]
Also writes tools/preview_<family>.png contact sheets (one row per blood colour) for eyeballing the textures.
Variant counts per family and the colour names live in FAMILIES / COLORS and must match the Lua scripts.
"""
import argparse
import math
import os
import random
import struct
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEX_DIR = os.path.join(ROOT, "textures", "MaxYari", "TheyBleed")
MESH_DIR = os.path.join(ROOT, "meshes", "MaxYari", "TheyBleed")

# name -> texture size (px), quad edge length (game units, before per-instance scaling), variant count.
# Keep texel density similar between families so ripples and droplet edges read the same in world space.
FAMILIES = {
    "drops": dict(tex_size=128, quad_size=16.0, count=12),  # small droplet clusters
    "pool": dict(tex_size=256, quad_size=40.0, count=8),    # bigger agglomerations
}

# Blood colours: (thin edge, thick core). Every colour gets the same droplet shapes.
# Non-red colours match Diverse Blood's effect textures (sampled dense-texel colour in comments), kept as dark as red.
COLORS = {
    "red": ((60, 2, 4), (40, 1, 3)),        # vanilla tx_blood (62, 0, 0)
    "blue": ((0, 50, 62), (0, 32, 41)),      # blood_blue (0, 56, 67)
    "green": ((20, 58, 2), (12, 38, 1)),     # blood_green (21, 66, 0)
    "dark": ((16, 5, 4), (7, 2, 2)),         # blood_dark (7, 0, 0): near-black ichor
    "orange": ((150, 50, 2), (104, 32, 1)),  # blood_orange (213, 70, 0), toned down so it reads as liquid, not paint
}
VARIANT_SHADE = (0.72, 1.12)  # per-texture brightness multipliers, spread evenly across a family's variants
THICK_RIM_PX = 9.0        # texels from the edge until blood reaches the core colour; tiny droplets stay edge-coloured

# PBR spec map (Wareya PBR): R metal, G roughness (squared by the shader), B ambient occlusion
ROUGHNESS = 0.02          # effective roughness after the shader squares G; near-mirror wet surface
METALNESS = 0.0           # liquid is dielectric; raising this trades diffuse colour for red-tinted reflections

# Normal map: flat liquid film with subtle surface ripples, so highlights wobble instead of mirroring flatly
RIPPLE_WAVES = 7                  # summed plane waves per texture
RIPPLE_WAVELENGTH = (10.0, 36.0)  # texels
RIPPLE_SLOPE = 0.06               # max surface slope contributed by all waves together
NORMAL_MAX_SIZE = 128             # ripples are smooth, so big decals don't need full-res normal maps
SPEC_SIZE = 4                     # spec values are constant; a tiny map is enough


# ---------------------------------------------------------------- textures

def make_droplet(rng, cx, cy, r):
    harmonics = [(k, rng.uniform(0.0, 0.16) / k ** 0.8, rng.uniform(0, math.tau)) for k in range(2, 7)]
    stretch = rng.uniform(1.0, 1.35)
    stretch_dir = rng.uniform(0, math.pi)
    return dict(cx=cx, cy=cy, r=r, harmonics=harmonics, stretch=stretch,
                cos=math.cos(stretch_dir), sin=math.sin(stretch_dir), shade=rng.uniform(0.93, 1.06))


def make_cluster(rng, size):
    half = size / 2
    drops = []
    main_r = rng.uniform(9, 20)
    # a few overlapping lobes form the main blob
    for _ in range(rng.randint(1, 3)):
        a = rng.uniform(0, math.tau)
        d = rng.uniform(0, main_r * 0.6)
        drops.append(make_droplet(rng, half + math.cos(a) * d, half + math.sin(a) * d,
                                  main_r * rng.uniform(0.55, 1.0)))
    # satellites, smaller the further out they are
    bias = rng.uniform(0, math.tau)  # clusters tend to spray in one direction
    for _ in range(rng.randint(5, 16)):
        a = bias + rng.gauss(0, 1.3)
        d = main_r + rng.expovariate(1 / 14)
        r = max(1.2, main_r * rng.uniform(0.12, 0.5) * (1 - min(d, 55) / 70))
        d = min(d, half - r - 3)
        drops.append(make_droplet(rng, half + math.cos(a) * d, half + math.sin(a) * d, r))
    # fine specks
    for _ in range(rng.randint(3, 12)):
        a = rng.uniform(0, math.tau)
        d = rng.uniform(main_r, half - 5)
        drops.append(make_droplet(rng, half + math.cos(a) * d, half + math.sin(a) * d, rng.uniform(0.8, 1.8)))
    return drops


def make_pool(rng, size):
    """A large irregular puddle built from many overlapping lobes, with splatter around it."""
    half = size / 2
    drops = []
    spread = rng.uniform(22, 42)
    lobe_r = (rng.uniform(14, 20), rng.uniform(26, 40))
    for _ in range(rng.randint(5, 10)):
        a = rng.uniform(0, math.tau)
        d = abs(rng.gauss(0, spread))
        r = rng.uniform(*lobe_r)
        d = min(d, half - r * 1.5 - 6)
        drops.append(make_droplet(rng, half + math.cos(a) * d, half + math.sin(a) * d, r))
    # runs/fingers leaking out from the pool edge
    for _ in range(rng.randint(2, 6)):
        a = rng.uniform(0, math.tau)
        d0 = spread + rng.uniform(5, 25)
        r = rng.uniform(6, 12)
        for step in range(rng.randint(2, 5)):
            d = min(d0 + step * r * 1.1, half - r * 1.5 - 4)
            drops.append(make_droplet(rng, half + math.cos(a) * d, half + math.sin(a) * d, r))
            r *= rng.uniform(0.65, 0.9)
    # satellites and specks
    bias = rng.uniform(0, math.tau)
    for _ in range(rng.randint(15, 35)):
        a = bias + rng.gauss(0, 1.6)
        d = spread + rng.uniform(15, half - 10)
        r = max(1.2, rng.uniform(1.5, 8) * (1 - min(d, half) / (half * 1.4)))
        d = min(d, half - r * 1.5 - 3)
        drops.append(make_droplet(rng, half + math.cos(a) * d, half + math.sin(a) * d, r))
    return drops


def make_ripples(rng):
    waves = []
    for _ in range(RIPPLE_WAVES):
        a = rng.uniform(0, math.tau)
        k = math.tau / rng.uniform(*RIPPLE_WAVELENGTH)
        waves.append((math.cos(a) * k, math.sin(a) * k, rng.uniform(0.4, 1.0), rng.uniform(0, math.tau)))
    # normalise so the steepest possible combined slope equals RIPPLE_SLOPE
    total = sum(amp * math.hypot(kx, ky) for kx, ky, amp, _ in waves)
    return [(kx, ky, amp * RIPPLE_SLOPE / total, ph) for kx, ky, amp, ph in waves]


def render_shape(drops, size):
    """Colour-independent fields: coverage, thickness (0 edge .. 1 core) and brightness per texel."""
    n = size
    cover = [0.0] * (n * n)
    thick = [0.0] * (n * n)
    shade_sum = [0.0] * (n * n)
    shade_w = [0.0] * (n * n)
    for dr in drops:
        rmax = dr["r"] * dr["stretch"] * 1.6 + 2
        x0, x1 = max(0, int(dr["cx"] - rmax)), min(n, int(dr["cx"] + rmax) + 1)
        y0, y1 = max(0, int(dr["cy"] - rmax)), min(n, int(dr["cy"] + rmax) + 1)
        for y in range(y0, y1):
            for x in range(x0, x1):
                dx, dy = x + 0.5 - dr["cx"], y + 0.5 - dr["cy"]
                # squash along stretch axis to get elongated droplets
                u = dx * dr["cos"] + dy * dr["sin"]
                v = -dx * dr["sin"] + dy * dr["cos"]
                u /= dr["stretch"]
                dist = math.hypot(u, v)
                theta = math.atan2(v, u)
                radius = dr["r"] * (1 + sum(amp * math.sin(k * theta + ph) for k, amp, ph in dr["harmonics"]))
                c = min(1.0, max(0.0, radius - dist + 0.5))  # 1px antialiased edge
                if c <= 0:
                    continue
                i = y * n + x
                inside = radius - dist
                # thickness from distance to this droplet's edge in texels, so merged lobes share one flat interior
                t = min(1.0, max(0.0, inside / THICK_RIM_PX))
                if c > cover[i]:
                    cover[i] = c
                if t > thick[i]:
                    thick[i] = t
                # blend brightness of overlapping droplets so merged lobes don't show hard patches
                w = c * (t + 0.05)
                shade_sum[i] += dr["shade"] * w
                shade_w[i] += w

    shade = [shade_sum[i] / shade_w[i] if shade_w[i] > 0 else 1.0 for i in range(n * n)]
    return cover, thick, shade


def colorize(shape, color, variant_shade):
    cover, thick, shade = shape
    edge, core = color
    diffuse = []
    for i in range(len(cover)):
        t = thick[i] ** 0.6
        t = t * t * (3 - 2 * t)
        k = shade[i] * variant_shade
        rgb = tuple(min(255.0, (e + (c - e) * t) * k) for e, c in zip(edge, core))
        diffuse.append((rgb[0], rgb[1], rgb[2], cover[i] * (0.9 + 0.1 * t) * 255))
    return diffuse


def render_normal(ripples, diffuse_size, size):
    """Ripple normal map at `size`, with ripples defined in diffuse texel units so they match across resolutions."""
    step = diffuse_size / size
    normal = []
    for y in range(size):
        for x in range(size):
            px, py = (x + 0.5) * step, (y + 0.5) * step
            # analytic gradient of the summed waves: h = sum(amp/k * sin(k.p + ph))
            hx = hy = 0.0
            for kx, ky, amp, ph in ripples:
                c = math.cos(kx * px + ky * py + ph) * amp / math.hypot(kx, ky)
                hx += c * kx
                hy += c * ky
            # OpenMW wants DirectX-style normal maps: +green points toward the bottom of the image,
            # which is also +row here, so both axes use the plain downhill gradient
            nx, ny, nz = -hx, -hy, 1.0
            ln = math.sqrt(nx * nx + ny * ny + nz * nz)
            normal.append(((nx / ln * 0.5 + 0.5) * 255, (ny / ln * 0.5 + 0.5) * 255, (nz / ln * 0.5 + 0.5) * 255, 255.0))
    return normal


def render_spec():
    return [(METALNESS * 255, math.sqrt(ROUGHNESS) * 255, 255.0, 255.0)] * (SPEC_SIZE * SPEC_SIZE)


def downsample(pixels, size, weights=None, renormalize=False):
    half = size // 2
    out = []
    for y in range(half):
        for x in range(half):
            idx = [(y * 2 + dy) * size + x * 2 + dx for dy in (0, 1) for dx in (0, 1)]
            quad = [pixels[j] for j in idx]
            w = [weights[j] for j in idx] if weights else [1.0] * 4
            w_sum = sum(w)
            if w_sum <= 0:  # fully transparent block: plain average
                w, w_sum = [1.0] * 4, 4.0
            # coverage-weighted colour keeps transparent texels from bleeding into edges at low mips
            rgb = [sum(p[c] * wi for p, wi in zip(quad, w)) / w_sum for c in range(3)]
            if renormalize:
                v = [c / 127.5 - 1 for c in rgb]
                ln = math.sqrt(sum(c * c for c in v)) or 1.0
                rgb = [(c / ln + 1) * 127.5 for c in v]
            out.append((rgb[0], rgb[1], rgb[2], sum(p[3] for p in quad) / 4))
    return out


def write_dds(path, pixels, size, renormalize=False):
    """Uncompressed A8R8G8B8 with the full mip chain down to 1x1; colour averaging is weighted by alpha."""
    levels = [pixels]
    s = size
    while s > 1:
        levels.append(downsample(levels[-1], s, [p[3] for p in levels[-1]], renormalize))
        s //= 2
    DDSD = 0x1 | 0x2 | 0x4 | 0x8 | 0x1000 | 0x20000
    header = struct.pack("<4sIIIIIII44x", b"DDS ", 124, DDSD, size, size, size * 4, 0, len(levels))
    header += struct.pack("<IIIIIIII", 32, 0x41, 0, 32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
    header += struct.pack("<IIII4x", 0x1000 | 0x400000 | 0x8, 0, 0, 0)
    assert len(header) == 128
    body = bytearray()
    for level in levels:
        for r, g, b, a in level:
            body += bytes((round(b), round(g), round(r), round(a)))
    with open(path, "wb") as f:
        f.write(header + body)


def write_preview(path, textures, n, cols=4):
    rows = (len(textures) + cols - 1) // cols
    w, h = cols * n, rows * n
    bg = (205, 195, 175)
    raw = bytearray()
    for py in range(h):
        raw.append(0)
        for px in range(w):
            idx = (py // n) * cols + px // n
            if idx >= len(textures):
                raw += bytes(bg)
                continue
            r, g, b, a = textures[idx][(py % n) * n + px % n]
            a /= 255
            raw += bytes(round(c * a + bgc * (1 - a)) for c, bgc in zip((r, g, b), bg))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


# ---------------------------------------------------------------- nif

class Nif:
    def __init__(self):
        self.blocks = []

    def add(self, type_name, data):
        self.blocks.append((type_name, data))
        return len(self.blocks) - 1

    def bytes(self, roots):
        out = bytearray(b"NetImmerse File Format, Version 4.0.0.2\n")
        out += struct.pack("<II", 0x04000002, len(self.blocks))
        for type_name, data in self.blocks:
            out += sstr(type_name) + data
        out += struct.pack("<I", len(roots)) + b"".join(struct.pack("<i", r) for r in roots)
        return bytes(out)


def sstr(s):
    b = s.encode("ascii")
    return struct.pack("<I", len(b)) + b


def obj_net(name, extra=-1, controller=-1):
    return sstr(name) + struct.pack("<ii", extra, controller)


def av_object(name, flags, properties, extra=-1):
    d = obj_net(name, extra)
    d += struct.pack("<H", flags)
    d += struct.pack("<3f", 0, 0, 0)
    d += struct.pack("<9f", 1, 0, 0, 0, 1, 0, 0, 0, 1)
    d += struct.pack("<f", 1.0)
    d += struct.pack("<3f", 0, 0, 0)  # velocity
    d += struct.pack("<I", len(properties)) + b"".join(struct.pack("<i", p) for p in properties)
    d += struct.pack("<I", 0)  # has bounding volume
    return d


def write_quad_nif(path, texture_path, quad_size):
    nif = Nif()
    h = quad_size / 2
    # indices are fixed by insertion order below
    ROOT, EXTRA, SWITCH, SHAPE, TEXPROP, MATPROP, ALPHAPROP, ZPROP, DATA, SOURCE = range(10)

    nif.add("NiNode", av_object("BloodDecal", 0x000C, [], extra=EXTRA) + struct.pack("<IiI", 1, SWITCH, 0))
    nif.add("NiStringExtraData", struct.pack("<iI", -1, len("NCO") + 4) + sstr("NCO"))  # no collision
    # Collision switch with the active-collision flag (0x20) cleared: OpenMW gives this subtree the effect
    # node mask, which keeps decals out of shadow casting and out of rendering raycasts.
    nif.add("NiCollisionSwitch", av_object("DecalSwitch", 0x000C, []) + struct.pack("<IiI", 1, SHAPE, 0))
    nif.add("NiTriShape", av_object("Decal", 0x0004, [TEXPROP, MATPROP, ALPHAPROP, ZPROP]) + struct.pack("<ii", DATA, -1))

    tex = obj_net("") + struct.pack("<HII", 0, 2, 7)  # flags, apply mode MODULATE, texture count
    tex += struct.pack("<I", 1)  # has base texture
    tex += struct.pack("<iIIIhhH", SOURCE, 0, 2, 0, 0, -75, 0)  # clamp S/T, trilinear, uv set 0
    tex += struct.pack("<6I", 0, 0, 0, 0, 0, 0)  # dark, detail, gloss, glow, bump, decal0
    nif.add("NiTexturingProperty", tex)

    mat = obj_net("") + struct.pack("<H", 0)
    mat += struct.pack("<3f3f3f3f", 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0)
    mat += struct.pack("<ff", 10.0, 1.0)
    nif.add("NiMaterialProperty", mat)

    # blend SRC_ALPHA / INV_SRC_ALPHA + alpha test GREATER threshold, so fully clear texels are discarded
    nif.add("NiAlphaProperty", obj_net("") + struct.pack("<HB", 0x12ED, 4))
    # depth test on, depth write off: overlapping decals don't fight each other
    nif.add("NiZBufferProperty", obj_net("") + struct.pack("<H", 0x0001))

    verts = [(-h, -h, 0), (h, -h, 0), (h, h, 0), (-h, h, 0)]
    uvs = [(0, 1), (1, 1), (1, 0), (0, 0)]
    data = struct.pack("<HI", len(verts), 1) + b"".join(struct.pack("<3f", *v) for v in verts)
    data += struct.pack("<I", 1) + b"".join(struct.pack("<3f", 0, 0, 1) for _ in verts)
    data += struct.pack("<3ff", 0, 0, 0, h * math.sqrt(2))
    data += struct.pack("<I", 0)  # vertex colours
    data += struct.pack("<HI", 1, 1)  # num uv sets, has uv
    data += b"".join(struct.pack("<2f", *uv) for uv in uvs)
    tris = [(0, 1, 2), (0, 2, 3)]
    data += struct.pack("<HI", len(tris), len(tris) * 3) + b"".join(struct.pack("<3H", *t) for t in tris)
    data += struct.pack("<H", 0)  # match groups
    nif.add("NiTriShapeData", data)

    src = obj_net("") + struct.pack("<B", 1) + sstr(texture_path)
    src += struct.pack("<IIIB", 5, 2, 3, 1)  # pixel layout default, mipmaps default, alpha default, static
    nif.add("NiSourceTexture", src)

    with open(path, "wb") as f:
        f.write(nif.bytes([ROOT]))


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1337)
    args = ap.parse_args()

    # generated outputs only: clear old files so renamed/removed variants don't linger
    for d in (TEX_DIR, MESH_DIR):
        os.makedirs(d, exist_ok=True)
        for f in os.listdir(d):
            # only decal outputs; logo.dds comes from make_logo.py
            if f.endswith((".dds", ".nif")) and f.startswith(tuple(f"{family}_" for family in FAMILIES)):
                os.remove(os.path.join(d, f))

    shapes = {"drops": make_cluster, "pool": make_pool}
    for family, cfg in FAMILIES.items():
        rng = random.Random(f"{args.seed}:{family}")  # per-family seed: editing one family doesn't reshuffle the other
        size = cfg["tex_size"]
        normal_size = min(size, NORMAL_MAX_SIZE)
        preview = {color: [] for color in COLORS}
        # evenly spaced shades, shuffled so darkness doesn't correlate with variant index
        lo, hi = VARIANT_SHADE
        shades = [lo + (hi - lo) * k / max(1, cfg["count"] - 1) for k in range(cfg["count"])]
        rng.shuffle(shades)
        for i in range(1, cfg["count"] + 1):
            shape = render_shape(shapes[family](rng, size), size)
            normal = render_normal(make_ripples(rng), size, normal_size)
            spec = render_spec()
            for color_name, color in COLORS.items():
                name = f"{family}_{color_name}_{i:02d}"
                diffuse = colorize(shape, color, shades[i - 1])
                preview[color_name].append(diffuse)
                write_dds(os.path.join(TEX_DIR, name + ".dds"), diffuse, size)
                # auto-picked up by OpenMW via 'specular map pattern' (_spec) and 'normal map pattern' (_n)
                write_dds(os.path.join(TEX_DIR, name + "_spec.dds"), spec, SPEC_SIZE)
                write_dds(os.path.join(TEX_DIR, name + "_n.dds"), normal, normal_size, renormalize=True)
                write_quad_nif(os.path.join(MESH_DIR, name + ".nif"),
                               f"textures\\MaxYari\\TheyBleed\\{name}.dds", cfg["quad_size"])
            print("wrote", family, i)
        rows = [tex for color_name in COLORS for tex in preview[color_name]]
        write_preview(os.path.join(ROOT, "tools", f"preview_{family}.png"), rows, size, cols=cfg["count"])


if __name__ == "__main__":
    main()
