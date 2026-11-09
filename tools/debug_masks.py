import sys
sys.path.insert(0, r"D:/Workspace/DeepSeek Harness/fn-fail2ban-upgrade/tools")
import make_icons as M
from PIL import Image, ImageDraw
px = 512
cx = px / 2
half_w = px * 0.300
height = px * 0.590
top = px * 0.180
cy = top + height * 0.50
outer = M.shield_path(cx, top, half_w, height)
inner = M.scale_about(outer, cx, cy, 0.80)
print("outer pts:", len(outer))
xs = [p[0] for p in outer]; ys = [p[1] for p in outer]
print("outer bbox x:", round(min(xs),1), round(max(xs),1), " y:", round(min(ys),1), round(max(ys),1))
print("inner bbox x:", round(min(p[0] for p in inner),1), round(max(p[0] for p in inner),1))
# render each mask separately into one strip for inspection
def mask_of(poly, invert=None):
    im = Image.new("L", (px, px), 0)
    d = ImageDraw.Draw(im)
    d.polygon(poly, fill=255)
    if invert:
        d.polygon(invert, fill=0)
    return im
rim = mask_of(outer, inner)
face = mask_of(inner)
slash = Image.new("L", (px, px), 0)
sd = ImageDraw.Draw(slash)
band = px * 0.132
for start in (px * 0.215, px * 0.215 + band * 2.0):
    sd.polygon([(start, 0), (start + band, 0), (0, start + band), (0, start)], fill=255)
face_white = Image.composite(slash, Image.new("L", (px, px), 0), face)
strip = Image.new("L", (px * 4, px), 0)
for i, m in enumerate([rim, face, slash, face_white]):
    strip.paste(m, (px * i, 0))
strip.save(r"D:/Workspace/DeepSeek Harness/fn-fail2ban-upgrade/image/_debug_masks.png")
print("saved")