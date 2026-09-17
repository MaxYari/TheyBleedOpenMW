#!/usr/bin/env python3
"""Render the THEY BLEED title logo: old horror-poster lettering with blood running off the letters.

Needs ImageMagick (`magick`) and the DejaVu fonts. Writes:
  textures/MaxYari/TheyBleed/logo.dds  (uncompressed, full mip chain; used by the settings page)
  imgs/they_bleed_logo.png              (same image, for pages/readmes)
and prints the content rectangle inside the texture, which menu.lua uses to crop the transparent padding.
"""
import argparse
import math
import os
import random
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FONT = "/usr/share/fonts/TTF/DejaVuSerifCondensed-Bold.ttf"
TITLE = "THEY BLEED"
SS = 2                      # supersampling factor; everything below is in final pixels unless noted
TEX_W, TEX_H = 1024, 512    # power-of-two canvas for the DDS; the logo is cropped back out in the UI
POINT_SIZE = 150
STRETCH_Y = 1.3             # tall, condensed poster lettering
DRIP_SPACE = 150            # room under the letters for the blood to run
PAD = 36                    # room for outline and shadow

TOP_RED = (205, 18, 22)
BOTTOM_RED = (92, 3, 8)


def write_dds_rect(path, pixels, w, h):
    """Uncompressed A8R8G8B8 DDS with the full mip chain (power-of-two sides), alpha-weighted colour averaging."""
    levels = [(w, h, pixels)]
    while w > 1 or h > 1:
        nw, nh = max(1, w // 2), max(1, h // 2)
        src = levels[-1][2]
        out = []
        for y in range(nh):
            for x in range(nw):
                quad = [src[min(y * 2 + dy, h - 1) * w + min(x * 2 + dx, w - 1)] for dy in (0, 1) for dx in (0, 1)]
                a = sum(p[3] for p in quad)
                if a > 0:
                    rgb = [sum(p[c] * p[3] for p in quad) / a for c in range(3)]
                else:
                    rgb = [sum(p[c] for p in quad) / 4 for c in range(3)]
                out.append((rgb[0], rgb[1], rgb[2], a / 4))
        levels.append((nw, nh, out))
        w, h = nw, nh
    W, H = levels[0][0], levels[0][1]
    header = struct.pack("<4sIIIIIII44x", b"DDS ", 124, 0x1 | 0x2 | 0x4 | 0x8 | 0x1000 | 0x20000, H, W, W * 4, 0,
                         len(levels))
    header += struct.pack("<IIIIIIII", 32, 0x41, 0, 32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
    header += struct.pack("<IIII4x", 0x1000 | 0x400000 | 0x8, 0, 0, 0)
    body = bytearray()
    for _, _, level in levels:
        for r, g, b, a in level:
            body += bytes((round(b), round(g), round(r), round(a)))
    with open(path, "wb") as f:
        f.write(header + body)


def magick(*args):
    subprocess.run(["magick", *args], check=True)


def read_gray(path):
    out = subprocess.run(["magick", path, "-colorspace", "Gray", "-depth", "8", "gray:-"],
                         check=True, capture_output=True).stdout
    w, h = map(int, subprocess.run(["magick", "identify", "-format", "%w %h", path],
                                   check=True, capture_output=True, text=True).stdout.split())
    return w, h, out


def write_pgm(path, w, h, values):
    with open(path, "wb") as f:
        f.write(b"P5 %d %d 255\n" % (w, h))
        f.write(bytes(max(0, min(255, round(v * 255))) for v in values))


def letter_spans(mask, w, h):
    """Column ranges of the individual glyphs (split on fully empty columns)."""
    spans, start = [], None
    for x in range(w):
        filled = any(mask[y * w + x] > 128 for y in range(h))
        if filled and start is None:
            start = x
        elif not filled and start is not None:
            spans.append((start, x))
            start = None
    if start is not None:
        spans.append((start, w))
    return spans


def bottom_edges(mask, w, h, x0, x1, text_bottom):
    """(x, y) where ink ends going downward, near the base of the letters (so blood can run straight down)."""
    edges = []
    for x in range(x0, x1):
        for y in range(h - 2, 0, -1):
            if mask[y * w + x] > 128 and mask[(y + 1) * w + x] <= 128:
                if y > text_bottom * 0.72:
                    edges.append((x, y))
                break
    return edges


def draw_drip(field, w, h, x0, y0, width, length, rng):
    """A blood run: flares out of the letter, thins as it falls, ends in a heavy bead."""
    bead = width * rng.uniform(0.62, 0.85)
    wobble_amp = rng.uniform(0, width * 0.25)
    wobble_freq = rng.uniform(0.01, 0.03)
    phase = rng.uniform(0, math.tau)
    flare = width * 0.9
    top = int(y0 - flare)
    bottom = int(y0 + length + bead + 2)
    for y in range(max(0, top), min(h, bottom)):
        t = min(1.0, max(0.0, (y - y0) / max(length, 1)))
        cx = x0 + math.sin((y - y0) * wobble_freq + phase) * wobble_amp * t
        hw = width / 2 * (1 - 0.5 * math.sqrt(t))
        if y < y0 + flare:  # concave fillet where the drip leaves the letter
            k = 1 - abs(y - y0) / flare
            hw += flare * 0.75 * k * k
        if y > y0 + length:  # no stem past the bead's centre
            hw = 0
        dy = y - (y0 + length)
        if abs(dy) < bead:
            hw = max(hw, math.sqrt(bead * bead - dy * dy))
        if hw <= 0:
            continue
        for x in range(max(0, int(cx - hw - 2)), min(w, int(cx + hw + 3))):
            c = min(1.0, max(0.0, hw - abs(x + 0.5 - cx) + 0.5))
            i = y * w + x
            if c > field[i]:
                field[i] = c
    return x0 + math.sin(length * wobble_freq + phase) * wobble_amp, y0 + length, bead


def draw_blob(field, w, h, cx, cy, r):
    for y in range(max(0, int(cy - r - 2)), min(h, int(cy + r + 3))):
        for x in range(max(0, int(cx - r - 2)), min(w, int(cx + r + 3))):
            c = min(1.0, max(0.0, r - math.hypot(x + 0.5 - cx, y + 0.5 - cy) + 0.5))
            i = y * w + x
            if c > field[i]:
                field[i] = c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    tmp = tempfile.mkdtemp(prefix="theybleed_logo_")
    t = lambda name: os.path.join(tmp, name)  # noqa: E731

    s = SS
    # 1. letters: tall condensed serif, trimmed, with room for drips below and effects around
    magick("-background", "black", "-fill", "white", "-font", FONT, "-pointsize", str(POINT_SIZE * s),
           "label:" + TITLE, "-trim", "+repage", "-resize", f"100%x{round(STRETCH_Y * 100)}%",
           "-gravity", "north", "-background", "black",
           "-splice", f"0x{PAD * s}", "-gravity", "south", "-splice", f"0x{(DRIP_SPACE + PAD) * s}",
           "-gravity", "center", "-bordercolor", "black", "-border", f"{PAD * s}x0",
           "-alpha", "off", t("text.png"))
    w, h, text = read_gray(t("text.png"))
    text_bottom = h - (DRIP_SPACE + PAD) * s

    # 2. blood runs off the bottoms of the letters; BLEED bleeds more than THEY, the final D pours
    drips = [0.0] * (w * h)
    spans = letter_spans(text, w, h)
    for idx, (x0, x1) in enumerate(spans):
        in_bleed = idx >= 4
        is_d = idx == len(spans) - 1
        edges = bottom_edges(text, w, h, x0, x1, text_bottom)
        if not edges:
            continue
        count = 5 if is_d else (rng.randint(1, 3) if in_bleed else rng.randint(0, 2))
        used = []
        for _ in range(count * 4):
            if len(used) >= count:
                break
            ex, ey = edges[rng.randrange(len(edges))]
            if any(abs(ex - u) < 26 * s for u in used):
                continue
            used.append(ex)
            if is_d and len(used) == 1:  # the pour: one thick run nearly to the bottom
                length = DRIP_SPACE - rng.uniform(8, 16)
                width = rng.uniform(24, 30)
            elif is_d:
                length = rng.uniform(40, DRIP_SPACE - 40)
                width = rng.uniform(10, 18)
            elif in_bleed:
                length = rng.uniform(18, DRIP_SPACE * 0.7)
                width = rng.uniform(7, 15)
            else:
                length = rng.uniform(10, 50)
                width = rng.uniform(5, 11)
            bx, by, bead = draw_drip(drips, w, h, ex, ey, width * s, length * s, rng)
            if length > 45 and rng.random() < 0.55:  # a drop that let go
                gap = rng.uniform(1.6, 3.2) * bead
                if by + gap + bead < h - PAD * s * 0.5:
                    draw_blob(drips, w, h, bx + rng.uniform(-2, 2) * s, by + gap, bead * rng.uniform(0.45, 0.7))
    write_pgm(t("drips.pgm"), w, h, drips)

    # 3. combine, distress the edges, add a few worn specks, shade for a wet look, colour, outline, shadow
    size = f"{w}x{h}"
    magick(t("text.png"), t("drips.pgm"), "-compose", "lighten", "-composite", "-alpha", "off", t("shape.png"))
    magick("-size", size, "xc:gray50", "-seed", str(args.seed), "+noise", "Random", "-colorspace", "Gray",
           "-blur", f"0x{1.2 * s}", "-normalize", "-alpha", "off", t("noise.png"))
    magick(t("shape.png"), "-blur", f"0x{1.5 * s}", t("noise.png"), "-compose", "blend",
           "-define", "compose:args=14,86", "-composite", "-threshold", "50%", "-blur", f"0x{0.6 * s}",
           "-alpha", "off", t("rough.png"))
    magick("-size", size, "xc:white", "-seed", str(args.seed + 1), "+noise", "Random", "-colorspace", "Gray",
           "-blur", f"0x{0.8 * s}", "-normalize", "-threshold", "97.5%", "-negate", "-blur", f"0x{0.5 * s}",
           "-alpha", "off", t("specks.png"))
    magick(t("rough.png"), t("specks.png"), "-compose", "multiply", "-composite", "-alpha", "off", t("alpha.png"))
    magick("-size", size, f"gradient:rgb{TOP_RED}-rgb{BOTTOM_RED}", t("grad.png"))
    magick(t("alpha.png"), "-blur", f"0x{3 * s}", "-shade", "115x40", "-auto-level", "-alpha", "off", t("shade.png"))
    magick(t("grad.png"), t("shade.png"), "-compose", "overlay", "-composite", t("alpha.png"),
           "-alpha", "off", "-compose", "copy-opacity", "-composite", t("color.png"))
    magick(t("alpha.png"), "-morphology", "Dilate", f"Disk:{3 * s}", "-blur", f"0x{0.8 * s}", "-alpha", "off", t("outline_a.png"))
    magick("-size", size, "xc:rgb(12,0,0)", t("outline_a.png"), "-alpha", "off", "-compose", "copy-opacity",
           "-composite", t("outline.png"))
    magick(t("alpha.png"), "-morphology", "Dilate", f"Disk:{4 * s}", "-blur", f"0x{9 * s}",
           "-evaluate", "multiply", "0.75", "-alpha", "off", t("shadow_a.png"))
    magick("-size", size, "xc:black", t("shadow_a.png"), "-alpha", "off", "-compose", "copy-opacity",
           "-composite", "-page", f"+{5 * s}+{8 * s}", t("shadow.png"))
    magick("-size", size, "xc:none", t("shadow.png"), "-compose", "over", "-composite",
           t("outline.png"), "-compose", "over", "-composite", t("color.png"), "-compose", "over", "-composite",
           "-resize", f"{100 // s}%", t("logo_full.png"))

    # 4. fit into the power-of-two texture, export PNG + DDS
    lw, lh = map(int, subprocess.run(["magick", "identify", "-format", "%w %h", t("logo_full.png")],
                                     check=True, capture_output=True, text=True).stdout.split())
    scale = min(1.0, (TEX_W - 8) / lw, (TEX_H - 8) / lh)
    cw, ch = int(lw * scale), int(lh * scale)
    ox, oy = (TEX_W - cw) // 2, (TEX_H - ch) // 2
    os.makedirs(os.path.join(ROOT, "imgs"), exist_ok=True)
    png = os.path.join(ROOT, "imgs", "they_bleed_logo.png")
    magick(t("logo_full.png"), "-resize", f"{cw}x{ch}!", "-background", "none", "-gravity", "center",
           "-extent", f"{TEX_W}x{TEX_H}", png)
    rgba = subprocess.run(["magick", png, "-depth", "8", "rgba:-"], check=True, capture_output=True).stdout
    pixels = [tuple(rgba[i:i + 4]) for i in range(0, len(rgba), 4)]
    tex_dir = os.path.join(ROOT, "textures", "MaxYari", "TheyBleed")
    os.makedirs(tex_dir, exist_ok=True)
    write_dds_rect(os.path.join(tex_dir, "logo.dds"), pixels, TEX_W, TEX_H)
    print(f"logo content rect: offset ({ox}, {oy}) size ({cw}, {ch}) in {TEX_W}x{TEX_H}")
    magick(png, "-background", "rgb(28,24,20)", "-flatten", os.path.join(tmp, "preview.png"))
    print("preview:", os.path.join(tmp, "preview.png"))


if __name__ == "__main__":
    main()
