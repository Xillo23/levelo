"""Icons drawn at runtime with Pillow (also used to build the .ico)."""

import base64
import io

BAR_HEIGHTS = (0.42, 0.78, 0.58, 0.78, 0.42)
BRAND_BLUE = (0, 103, 192, 255)


def make_icon(size=64, fg=(255, 255, 255, 255), bg=None, dim=False):
    from PIL import Image, ImageDraw
    S = size * 4  # supersampling
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = int(S * 0.06)
    if bg is not None:
        d.rounded_rectangle((0, 0, S - 1, S - 1), radius=int(S * 0.24), fill=bg)
        pad = int(S * 0.22)
    inner = S - 2 * pad
    n = len(BAR_HEIGHTS)
    bw = inner / (n * 1.6)
    gap = (inner - n * bw) / (n - 1)
    color = fg if not dim else (fg[0], fg[1], fg[2], 120)
    for i, h in enumerate(BAR_HEIGHTS):
        if dim:
            h = 0.2
        x0 = pad + i * (bw + gap)
        bh = inner * h
        y0 = pad + (inner - bh) / 2
        d.rounded_rectangle((x0, y0, x0 + bw, y0 + bh), radius=bw / 2, fill=color)
    return img.resize((size, size), Image.LANCZOS)


def app_icon(size=256):
    return make_icon(size, fg=(255, 255, 255, 255), bg=BRAND_BLUE)


def png_b64(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue())
