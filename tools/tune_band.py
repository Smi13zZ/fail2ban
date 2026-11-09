import sys
sys.path.insert(0, r"D:/Workspace/DeepSeek Harness/fn-fail2ban-upgrade/tools")
import make_icons as M
from PIL import Image, ImageDraw
px = 512
cx = px/2; half_w = px*0.300; height = px*0.590; top = px*0.180; cy = top + height*0.50
outer = M.shield_path(cx, top, half_w, height)
inner = M.scale_about(outer, cx, cy, 0.80)
face = Image.new("L",(px,px),0); ImageDraw.Draw(face).polygon(inner, fill=255)
area = sum(1 for v in face.get_flattened_data() if v>0)
print("face area:", area)
# sweep the band offset and report how much of the face each white band covers
band = px*0.132
best = []
for k in range(0, 20):
    start = px*0.05 + k*px*0.08
    slash = Image.new("L",(px,px),0); sd=ImageDraw.Draw(slash)
    for s in (start, start + band*2.0):
        sd.polygon([(s,0),(s+band,0),(0,s+band),(0,s)], fill=255)
    fw = Image.composite(slash, Image.new("L",(px,px),0), face)
    cov = sum(1 for v in fw.get_flattened_data() if v>0)
    best.append((cov, round(start/px,3)))
for cov, s in sorted(best, reverse=True)[:6]:
    print("start=%.3f*px  white=%.1f%% of face" % (s, cov*100.0/area))