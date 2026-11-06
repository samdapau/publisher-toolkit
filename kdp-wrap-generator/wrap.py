#!/usr/bin/env python3
"""KDP Paperback Full-Wrap Cover Generator.

Builds a print-ready full-wrap cover (back + spine + front) for an Amazon KDP
paperback from a finished front-cover image, using the standard KDP paperback
cover template geometry:

    total width  = bleed + trim + spine + trim + bleed
    total height = bleed + trim + bleed          (bleed = 0.125 in)

Bleed exists on the OUTER edges only -- never on the spine side of a panel.
Each panel is composed on its TRIM box; the bleed strips are filled by
extending the artwork's edge pixels outward, so nothing is ever stretched
out of proportion.

Spine width = page count x paper factor (KDP):
    white paper  0.002252 in/page
    cream paper  0.002500 in/page
    color paper  0.002347 in/page

The barcode is printed by KDP itself in the lower spine-side corner of the
back cover (2 x 1.2 in). This tool never draws a barcode box in the print
files; it keeps a 2.6 x 1.8 in zone in that corner free of content and shows
the zone in the guide preview only.

Requires: Python 3.9+ and Pillow (pip install pillow). Nothing else.
Free software. No ads, no tracking, no network access -- works fully offline.
"""

import argparse
import os
import sys
import time

from PIL import Image, ImageDraw, ImageFont

DPI = 300
BLEED_IN = 0.125

# One pixel note: 0.125 in at 300 DPI is 37.5 px. Production files floor the
# leading bleed to 37 px and give the remaining pixel to the trailing edge,
# which reproduces KDP's own template dimensions exactly (e.g. a 168-page
# 6x9 white-paper wrap is 3788 x 2775 px, not 3789 x 2775).
BLEED_PX = int(BLEED_IN * DPI)  # 37

PAPER_FACTORS = {  # inches of spine per page (KDP paperback)
    "white": 0.002252,
    "cream": 0.002500,
    "color": 0.002347,
}

TRIM_SIZES = {  # name -> (width, height) in inches
    "5x8": (5.0, 8.0),
    "5.5x8.5": (5.5, 8.5),
    "6x9": (6.0, 9.0),
    "6.14x9.21": (6.14, 9.21),
    "7x10": (7.0, 10.0),
    "8.5x11": (8.5, 11.0),
}

SAFE_IN = 0.5            # keep text at least this far from every trim edge
SPINE_TEXT_MIN_IN = 0.35  # below this spine width, spine text is skipped
BARCODE_W_IN, BARCODE_H_IN = 2.0, 1.2      # the barcode KDP prints
KEEPOUT_W_IN, KEEPOUT_H_IN = 2.6, 1.8      # zone kept free around it


# --------------------------------------------------------------------------
# Fonts: look in a local fonts/ folder first, then standard system locations.
# --------------------------------------------------------------------------
_FONT_NAMES = [
    "DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "LiberationSerif-Regular.ttf",
    "LiberationSerif-Bold.ttf", "LiberationSans-Regular.ttf",
    "LiberationSans-Bold.ttf", "Arial.ttf", "Helvetica.ttf",
]
_FONT_DIRS = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts"),
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/truetype/liberation",
    "/Library/Fonts",
    os.path.expanduser("~/Library/Fonts"),
    "C:\\Windows\\Fonts",
]


def load_font(size, bold=False):
    """Best-effort TrueType font; falls back to Pillow's built-in font."""
    names = [n for n in _FONT_NAMES if ("Bold" in n) == bold] or _FONT_NAMES
    for name in names:
        for d in _FONT_DIRS:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                try:
                    return ImageFont.truetype(p, size)
                except OSError:
                    pass
    return ImageFont.load_default()


def parse_color(text):
    text = text.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    if len(text) != 6:
        raise ValueError(f"bad color '{text}' (use hex, e.g. #1a1a1a)")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def wrap_text(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_w:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


# --------------------------------------------------------------------------
# Artwork placement: uniform cover-fit into the trim box (never a bare
# resize -- that would distort the art), then edge-extension into the bleed.
# --------------------------------------------------------------------------
def fit_to_trim(art, trim_w_px, trim_h_px):
    s = max(trim_w_px / art.width, trim_h_px / art.height)
    scaled = art.resize((round(art.width * s), round(art.height * s)),
                        Image.LANCZOS)
    x = (scaled.width - trim_w_px) // 2
    y = (scaled.height - trim_h_px) // 2
    return scaled.crop((x, y, x + trim_w_px, y + trim_h_px))


def extend_bleed(trim_img, left, top, right, bottom):
    """Surround a trim-sized image with bleed strips made from its own
    outermost pixel rows/columns (the standard edge-extension fill)."""
    w, h = trim_img.size
    panel = Image.new("RGB", (w + left + right, h + top + bottom))
    panel.paste(trim_img, (left, top))
    if left:
        panel.paste(trim_img.crop((0, 0, 1, h)).resize((left, h)), (0, top))
    if right:
        panel.paste(trim_img.crop((w - 1, 0, w, h)).resize((right, h)),
                    (left + w, top))
    pw = panel.width
    if top:
        panel.paste(panel.crop((0, top, pw, top + 1)).resize((pw, top)),
                    (0, 0))
    if bottom:
        panel.paste(
            panel.crop((0, top + h - 1, pw, top + h)).resize((pw, bottom)),
            (0, top + h))
    return panel


# --------------------------------------------------------------------------
# Back cover from plain color + optional text file.
# Text file format: first line = headline; blank line separates paragraphs.
# --------------------------------------------------------------------------
def build_back_from_text(size, color, text_path, notes):
    img = Image.new("RGB", size, color)
    if not text_path:
        return img
    with open(text_path, encoding="utf-8") as fh:
        blocks = [b.strip() for b in fh.read().split("\n\n") if b.strip()]
    if not blocks:
        return img
    d = ImageDraw.Draw(img)
    lum = (0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2]) / 255
    ink = (20, 20, 20) if lum > 0.6 else (240, 238, 232)
    pw, ph = size
    inset = int(SAFE_IN * DPI)
    x0, x1 = inset, pw - inset
    y = inset
    bottom_limit = ph - inset
    headline = " ".join(blocks[0].split())
    f_head = load_font(64, bold=True)
    for ln in wrap_text(d, headline, f_head, x1 - x0):
        d.text((x0, y), ln, font=f_head, fill=ink)
        y += 86
    y += 30
    f_body = load_font(40)
    for para in blocks[1:]:
        for ln in wrap_text(d, " ".join(para.split()), f_body, x1 - x0):
            if y + 52 > bottom_limit:
                notes.append("back-cover text truncated to fit the panel")
                return img
            d.text((x0, y), ln, font=f_body, fill=ink)
            y += 56
        y += 28
    return img


# --------------------------------------------------------------------------
# Guide preview: a designed overlay on a copy of the wrap. Guides appear
# ONLY here -- never in the print PNG/PDF.
# --------------------------------------------------------------------------
def dashed_rect(d, box, fill, width=3, dash=26, gap=16):
    x0, y0, x1, y1 = box
    x = x0
    while x < x1:
        d.line([x, y0, min(x + dash, x1), y0], fill=fill, width=width)
        d.line([x, y1, min(x + dash, x1), y1], fill=fill, width=width)
        x += dash + gap
    y = y0
    while y < y1:
        d.line([x0, y, x0, min(y + dash, y1)], fill=fill, width=width)
        d.line([x1, y, x1, min(y + dash, y1)], fill=fill, width=width)
        y += dash + gap


def tag(d, xy, text, font, bg, fg=(255, 255, 255)):
    x, y = xy
    w = d.textlength(text, font=font)
    d.rounded_rectangle([x, y, x + w + 24, y + 40], radius=8, fill=bg)
    d.text((x + 12, y + 7), text, font=font, fill=fg)


def draw_preview(wrap_img, g, out_path):
    prev = wrap_img.copy()
    d = ImageDraw.Draw(prev, "RGBA")
    f_tag = load_font(26, bold=True)
    ink = (26, 26, 26)
    teal = (0, 128, 128)
    red = (178, 34, 34)
    gold = (160, 120, 20)

    for name, tx0 in (("BACK COVER", g["back_trim_x"]),
                      ("FRONT COVER", g["front_trim_x"])):
        ty0 = g["top_bleed"]
        tx1, ty1 = tx0 + g["trim_w_px"], ty0 + g["trim_h_px"]
        # trim box: dark outline with a white core line
        d.rectangle([tx0, ty0, tx1, ty1], outline=(255, 255, 255), width=5)
        d.rectangle([tx0, ty0, tx1, ty1], outline=ink, width=2)
        # 0.5 in safe zone
        s = int(SAFE_IN * DPI)
        dashed_rect(d, [tx0 + s, ty0 + s, tx1 - s, ty1 - s], teal)
        tag(d, (tx0 + 14, ty0 + 14), name, f_tag, ink)
        tag(d, (tx0 + s + 14, ty0 + s + 14), "TEXT SAFE ZONE (0.5 in)",
            f_tag, teal)

    # spine folds
    sx0, sx1 = g["spine_x"], g["spine_x"] + g["spine_px"]
    for x in (sx0, sx1):
        d.line([x, 0, x, prev.height], fill=(255, 255, 255), width=5)
        d.line([x, 0, x, prev.height], fill=gold, width=2)
    if g["spine_px"] >= 60:
        strip = Image.new("RGBA", (340, 44), (0, 0, 0, 0))
        sd = ImageDraw.Draw(strip)
        sd.rounded_rectangle([0, 0, 340, 44], radius=8, fill=gold + (255,))
        sd.text((12, 8), "SPINE", font=f_tag, fill=(255, 255, 255))
        rot = strip.rotate(90, expand=True)
        prev.paste(rot, (sx0 + (g["spine_px"] - rot.width) // 2, 60), rot)
        d = ImageDraw.Draw(prev, "RGBA")

    # barcode keep-out zone (back cover, spine-side bottom corner)
    bx1 = g["back_trim_x"] + g["trim_w_px"]
    by1 = g["top_bleed"] + g["trim_h_px"]
    kw, kh = int(KEEPOUT_W_IN * DPI), int(KEEPOUT_H_IN * DPI)
    d.rectangle([bx1 - kw, by1 - kh, bx1, by1], fill=red + (38,))
    dashed_rect(d, [bx1 - kw, by1 - kh, bx1, by1], red)
    m = int(0.25 * DPI)
    bw, bh = int(BARCODE_W_IN * DPI), int(BARCODE_H_IN * DPI)
    d.rectangle([bx1 - m - bw, by1 - m - bh, bx1 - m, by1 - m],
                outline=red, width=3)
    tag(d, (bx1 - kw + 14, by1 - kh + 14),
        "BARCODE 2 x 1.2 in - KDP prints here, keep clear", f_tag, red)

    prev.save(out_path, quality=90)


# --------------------------------------------------------------------------
def build(args):
    notes = []
    trim_w_in, trim_h_in = TRIM_SIZES[args.trim]
    spine_in = args.pages * PAPER_FACTORS[args.paper]
    spine_px = int(round(spine_in * DPI))
    trim_w_px = int(round(trim_w_in * DPI))
    trim_h_px = int(round(trim_h_in * DPI))
    total_w_px = 2 * (BLEED_PX + trim_w_px) + spine_px
    total_h_px = int(round((trim_h_in + 2 * BLEED_IN) * DPI))
    top_bleed = BLEED_PX
    bottom_bleed = total_h_px - top_bleed - trim_h_px

    wrap_img = Image.new("RGB", (total_w_px, total_h_px), (24, 24, 24))
    front_trim_x = BLEED_PX + trim_w_px + spine_px
    back_trim_x = BLEED_PX

    front = Image.open(args.front).convert("RGB")
    front_trim = fit_to_trim(front, trim_w_px, trim_h_px)
    front_panel = extend_bleed(front_trim, 0, top_bleed, BLEED_PX,
                               bottom_bleed)
    wrap_img.paste(front_panel, (front_trim_x, 0))

    # ---- back panel ----
    back_panel_w = BLEED_PX + trim_w_px
    if args.back:
        back = Image.open(args.back).convert("RGB")
        back_trim = fit_to_trim(back, trim_w_px, trim_h_px)
        back_panel = extend_bleed(back_trim, BLEED_PX, top_bleed, 0,
                                  bottom_bleed)
    else:
        color = (parse_color(args.back_color) if args.back_color
                 else (246, 241, 230))
        back_panel = build_back_from_text(
            (back_panel_w, total_h_px), color, args.back_text, notes)
        # note: plain backs are drawn panel-sized; text inset is measured
        # from the panel edges, which include the 0.125 in bleed.
    wrap_img.paste(back_panel, (0, 0))

    # ---- spine ----
    if args.spine_color:
        spine_col = parse_color(args.spine_color)
    else:
        edge = front_trim.crop((0, 0, 1, trim_h_px)).resize((1, 1))
        avg = edge.getpixel((0, 0))
        spine_col = tuple(int(c * 0.5) for c in avg)  # darkened edge tone
    d = ImageDraw.Draw(wrap_img)
    spine_x = BLEED_PX + trim_w_px
    d.rectangle([spine_x, 0, spine_x + spine_px - 1, total_h_px],
                fill=spine_col)

    spine_drawn = False
    if args.spine_text:
        if spine_in < SPINE_TEXT_MIN_IN:
            notes.append(
                f"spine text skipped: spine is {spine_in:.3f} in "
                f"(< {SPINE_TEXT_MIN_IN} in minimum)")
        else:
            fold_clear = int(0.0625 * DPI)
            end_clear = int(0.375 * DPI)
            size = max(12, int(spine_px * 0.62))
            while size > 10:
                f = load_font(size, bold=True)
                bb = d.textbbox((0, 0), args.spine_text, font=f)
                if (bb[3] - bb[1] <= spine_px - 2 * fold_clear
                        and bb[2] - bb[0] <= total_h_px - 2 * end_clear):
                    break
                size -= 2
            strip = Image.new("RGBA", (bb[2] - bb[0] + 16, bb[3] - bb[1] + 16),
                              (0, 0, 0, 0))
            sd = ImageDraw.Draw(strip)
            lum = (0.299 * spine_col[0] + 0.587 * spine_col[1]
                   + 0.114 * spine_col[2]) / 255
            ink = (24, 24, 24) if lum > 0.6 else (242, 238, 228)
            sd.text((8 - bb[0], 8 - bb[1]), args.spine_text, font=f, fill=ink)
            rot = strip.rotate(90, expand=True)  # reads bottom-to-top (US)
            wrap_img.paste(rot, (spine_x + (spine_px - rot.width) // 2,
                                 (total_h_px - rot.height) // 2), rot)
            spine_drawn = True

    # ---- outputs ----
    png_path = f"{args.out}-wrap.png"
    pdf_path = f"{args.out}-wrap.pdf"
    prev_path = f"{args.out}-preview.jpg"
    wrap_img.save(png_path)
    wrap_img.save(pdf_path, "PDF", resolution=float(DPI))
    g = dict(back_trim_x=back_trim_x, front_trim_x=front_trim_x,
             top_bleed=top_bleed, trim_w_px=trim_w_px, trim_h_px=trim_h_px,
             spine_x=spine_x, spine_px=spine_px)
    draw_preview(wrap_img, g, prev_path)

    return dict(spine_in=spine_in, total_w_px=total_w_px,
                total_h_px=total_h_px, trim=(trim_w_in, trim_h_in),
                spine_drawn=spine_drawn, notes=notes,
                files=(png_path, pdf_path, prev_path))


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="wrap.py",
        description="Build a print-ready KDP paperback full-wrap cover "
                    "(back + spine + front) at 300 DPI.",
        epilog="Example:\n"
               "  python3 wrap.py --front front.jpg --pages 168 "
               "--trim 6x9 --paper white \\\n"
               "      --spine-text \"My Title / Jane Author\" "
               "--out my-book",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--front", required=True,
                    help="front cover image (any size; fitted without "
                         "distortion)")
    ap.add_argument("--back", help="back cover image (optional)")
    ap.add_argument("--back-color", default=None,
                    help="hex color for a plain back cover, e.g. #f6f1e6 "
                         "(used when --back is not given)")
    ap.add_argument("--back-text", default=None,
                    help="UTF-8 text file for a plain back cover: first "
                         "block = headline, blank line between paragraphs")
    ap.add_argument("--pages", required=True, type=int,
                    help="final printed page count of the interior PDF")
    ap.add_argument("--trim", default="6x9", choices=sorted(TRIM_SIZES),
                    help="trim size in inches (default: 6x9)")
    ap.add_argument("--paper", default="white",
                    choices=sorted(PAPER_FACTORS),
                    help="interior paper (default: white)")
    ap.add_argument("--spine-text", default=None,
                    help="text for the spine, e.g. \"Title / Author\" "
                         "(drawn when the spine is at least 0.35 in wide)")
    ap.add_argument("--spine-color", default=None,
                    help="hex color for the spine (default: sampled from "
                         "the front cover's spine-side edge, darkened)")
    ap.add_argument("--out", required=True,
                    help="output prefix; writes <out>-wrap.png, "
                         "<out>-wrap.pdf and <out>-preview.jpg")
    args = ap.parse_args(argv)

    if args.pages < 24:
        print("Error: KDP paperbacks need at least 24 pages.",
              file=sys.stderr)
        return 2

    t0 = time.time()
    try:
        r = build(args)
    except FileNotFoundError as e:
        print(f"Error: file not found: {e.filename}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    tw, th = r["trim"]
    print("KDP Paperback Wrap Generator")
    print(f"  Trim ......... {tw:g} x {th:g} in")
    print(f"  Pages ........ {args.pages} ({args.paper})")
    print(f"  Spine ........ {r['spine_in']:.3f} in"
          + (" (with text)" if r["spine_drawn"] else ""))
    print(f"  Wrap ......... {tw * 2 + r['spine_in'] + 0.25:.3f} x "
          f"{th + 0.25:.2f} in  =  {r['total_w_px']} x {r['total_h_px']} px"
          " @ 300 DPI")
    for n in r["notes"]:
        print(f"  Note ......... {n}")
    for p in r["files"]:
        print(f"  Wrote ........ {p} ({os.path.getsize(p):,} bytes)")
    print(f"  Done in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
