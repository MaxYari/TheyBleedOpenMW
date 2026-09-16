#!/usr/bin/env python3
"""Generate blood droplet-cluster textures (DDS) and matching decal quads (NIF 4.0.0.2).

Stdlib only. Run from anywhere:  python3 tools/generate_assets.py [--seed N] [--count N]
Also writes tools/preview.png (contact sheet) for eyeballing the textures.
"""
import argparse
import math
import os
import random
import struct
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEX_DIR = os.path.join(ROOT, "textures", "MaxYari", "BloodDecals")
MESH_DIR = os.path.join(ROOT, "meshes", "MaxYari", "BloodDecals")

TEX_SIZE = 128
QUAD_SIZE = 16.0  # game units, edge length of the quad before per-instance scaling

EDGE_COLOR = (120, 14, 12)  # thin blood at droplet edges
CORE_COLOR = (62, 3, 5)     # thick blood in droplet centres


# ---------------------------------------------------------------- textures

def make_droplet(rng, cx, cy, r):
    harmonics = [(k, rng.uniform(0.0, 0.16) / k ** 0.8, rng.uniform(0, math.tau)) for k in range(2, 7)]
    stretch = rng.uniform(1.0, 1.35)
    stretch_dir = rng.uniform(0, math.pi)
    return dict(cx=cx, cy=cy, r=r, harmonics=harmonics, stretch=stretch,
                cos=math.cos(stretch_dir), sin=math.sin(stretch_dir), shade=rng.uniform(0.85, 1.1))


def make_cluster(rng):
    half = TEX_SIZE / 2
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


def render_cluster(drops):
    n = TEX_SIZE
    cover = [0.0] * (n * n)
    thick = [0.0] * (n * n)
    shade = [1.0] * (n * n)
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
                t = min(1.0, max(0.0, (radius - dist) / max(radius, 1e-3)))
                if c > cover[i]:
                    cover[i] = c
                if t > thick[i]:
                    thick[i] = t
                    shade[i] = dr["shade"]
    pixels = []
    for i in range(n * n):
        t = thick[i] ** 0.6
        t = t * t * (3 - 2 * t)
        rgb = tuple(min(255.0, (e + (c - e) * t) * shade[i]) for e, c in zip(EDGE_COLOR, CORE_COLOR))
        alpha = cover[i] * (0.82 + 0.16 * t)
        pixels.append((rgb[0], rgb[1], rgb[2], alpha * 255))
    return pixels


def downsample(pixels, size):
    half = size // 2
    out = []
    for y in range(half):
        for x in range(half):
            quad = [pixels[(y * 2 + dy) * size + x * 2 + dx] for dy in (0, 1) for dx in (0, 1)]
            a_sum = sum(p[3] for p in quad)
            if a_sum > 0:  # alpha-weighted colour keeps transparent texels from darkening edges
                rgb = [sum(p[c] * p[3] for p in quad) / a_sum for c in range(3)]
            else:
                rgb = [sum(p[c] for p in quad) / 4 for c in range(3)]
            out.append((rgb[0], rgb[1], rgb[2], a_sum / 4))
    return out


def write_dds(path, pixels, size):
    levels = [pixels]
    s = size
    while s > 1:
        levels.append(downsample(levels[-1], s))
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


def write_preview(path, textures, cols=4):
    n = TEX_SIZE
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


def write_quad_nif(path, texture_path):
    nif = Nif()
    h = QUAD_SIZE / 2
    # indices are fixed by insertion order below
    ROOT, EXTRA, SHAPE, TEXPROP, MATPROP, ALPHAPROP, ZPROP, DATA, SOURCE = range(9)

    nif.add("NiNode", av_object("BloodDecal", 0x000C, [], extra=EXTRA) + struct.pack("<IiI", 1, SHAPE, 0))
    nif.add("NiStringExtraData", struct.pack("<iI", -1, len("NCO") + 4) + sstr("NCO"))  # no collision
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
    nif.add("NiAlphaProperty", obj_net("") + struct.pack("<HB", 0x12ED, 8))
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
    ap.add_argument("--count", type=int, default=12)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    os.makedirs(TEX_DIR, exist_ok=True)
    os.makedirs(MESH_DIR, exist_ok=True)
    textures = []
    for i in range(1, args.count + 1):
        name = f"drops_{i:02d}"
        pixels = render_cluster(make_cluster(rng))
        textures.append(pixels)
        write_dds(os.path.join(TEX_DIR, name + ".dds"), pixels, TEX_SIZE)
        write_quad_nif(os.path.join(MESH_DIR, name + ".nif"), f"textures\\MaxYari\\BloodDecals\\{name}.dds")
        print("wrote", name)
    write_preview(os.path.join(ROOT, "tools", "preview.png"), textures)


if __name__ == "__main__":
    main()
