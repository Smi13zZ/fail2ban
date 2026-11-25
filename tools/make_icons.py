#!/usr/bin/env python3
"""Render the Fail2ban app icon set from the supplied 1024 px artwork.

The supplied master is a flat two-colour drawing (pure black mark on pure
white).  It is used directly as the source for every emitted size instead of
being re-traced: re-drawing curves by hand would drift from the artwork the
icon was chosen for.

Two details matter for the small sizes:

  * LANCZOS downscaling of a 40 px rim to 16 px leaves the rim as a grey haze
    that reads as a smudge.  Sizes up to 48 px therefore get a symmetric
    contrast curve that pushes near-black to black and near-white to white,
    which keeps the mark legible in the app center's small tiles.
  * the artwork is a square white field; the app center draws icons on a dark
    background, so a square white tile stands out next to the other panels'
    rounded ones.  Every size is therefore masked to a rounded tile whose radius
    is 30% of the edge, matching the radius measured on the sibling icons.

fnOS resolves images/icon_{0}.png with {0} in 16/24/48/64/72/96/128/256/512;
a missing size leaves a blank tile on some densities, so the whole set is
written on every run.  ICON.PNG (64) and ICON_256.PNG (256) sit at the package
root because fnpack requires them there separately.
"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(HERE, "src", "pkg", "fn-fail2ban")
IMAGES = os.path.join(PKG, "app", "ui", "images")
WEB = os.path.join(PKG, "app", "web")
MASTER = os.path.join(HERE, "image", "icon-master-1024.png")

SIZES = (16, 24, 48, 64, 72, 96, 128, 256, 512)

# curve applied to sizes <= 48 px: 0 below LO, 255 above HI, smooth in between
LO, HI = 96, 160

#: 圆角半径占边长的比例。
#: 用逐行轮廓与兄弟图标对齐搜出来的：0.24 时平均偏差 0.63px、最大 1px。
#: （先前按单点拟合估 0.30，逐行比对方才看出偏大，那条路不准。）
CORNER_RATIO = 0.24
#: 遮罩超采样倍数。直接在小尺寸上画圆角会留一像素锯齿。
SUPERSAMPLE = 8


def _crisp(img):
    def curve(v):
        if v <= LO:
            return 0
        if v >= HI:
            return 255
        t = (v - LO) / float(HI - LO)
        t = t * t * (3.0 - 2.0 * t)
        return int(round(t * 255))
    lut = [curve(v) for v in range(256)]
    return img.point(lut * 3)


def rounded(img, ratio=CORNER_RATIO):
    """把方形图叠到圆角遮罩上，返回 RGBA。

    母版是方形白底、四角不透明；应用中心用深色底，方形白瓦片会和
    其它面板的圆角图标明显不一样。遮罩按 SUPERSAMPLE 倍放大再缩回，
    边缘才有抗锯齿。
    """
    w, h = img.size
    r = max(1, int(round(min(w, h) * ratio)))
    big = (w * SUPERSAMPLE, h * SUPERSAMPLE)
    mask = Image.new("L", big, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, big[0] - 1, big[1] - 1], radius=r * SUPERSAMPLE, fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def render(master, size):
    img = master.resize((size, size), Image.LANCZOS)
    if size <= 48:
        img = _crisp(img)
    return rounded(img)


def main():
    master = Image.open(MASTER).convert("RGB")
    if master.size != (1024, 1024):
        raise SystemExit("master must be 1024x1024, got %r" % (master.size,))
    os.makedirs(IMAGES, exist_ok=True)
    os.makedirs(WEB, exist_ok=True)

    # stale artwork from earlier icon generations
    for name in ("icon-64.png", "icon-256.png", "logo.svg", "favicon.svg"):
        path = os.path.join(IMAGES if name.startswith("icon-") else WEB, name)
        if os.path.exists(path):
            os.remove(path)

    for size in SIZES:
        render(master, size).save(
            os.path.join(IMAGES, "icon_%d.png" % size), "PNG", optimize=True)
    rounded(master.resize((64, 64), Image.LANCZOS)).save(
        os.path.join(PKG, "ICON.PNG"), "PNG", optimize=True)
    rounded(master.resize((256, 256), Image.LANCZOS)).save(
        os.path.join(PKG, "ICON_256.PNG"), "PNG", optimize=True)
    rounded(master.resize((128, 128), Image.LANCZOS)).save(
        os.path.join(WEB, "logo.png"), "PNG", optimize=True)
    render(master, 32).save(os.path.join(WEB, "favicon-32.png"), "PNG", optimize=True)
    master.resize((180, 180), Image.LANCZOS).save(os.path.join(WEB, "apple-touch-icon.png"), "PNG", optimize=True)
    print("wrote %d icon sizes + ICON.PNG + ICON_256.PNG + 3 web assets" % len(SIZES))


if __name__ == "__main__":
    main()
