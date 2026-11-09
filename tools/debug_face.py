import sys
sys.path.insert(0, r"D:/Workspace/DeepSeek Harness/fn-fail2ban-upgrade/tools")
import make_icons as M
from PIL import Image, ImageDraw
px = 512
cx = px/2; half_w = px*0.300; height = px*0.590; top = px*0.180; cy = top + height*0.50
outer = M.shield_path(cx, top, half_w, height)
inner = M.scale_about(outer, cx, cy, 0.80)
zero = Image.new("L",(px,px),0)
face = Image.new("L",(px,px),0); ImageDraw.Draw(face).polygon(inner, fill=255)
slash = Image.new("L",(px,px),0); sd=ImageDraw.Draw(slash)
band = px*0.132
for start in (px*0.215, px*0.215 + band*2.0):
    sd.polygon([(start,0),(start+band,0),(0,start+band),(0,start)], fill=255)
print("slash extrema:", slash.getextrema())
print("face extrema:", face.getextrema())
fw = Image.composite(slash, zero, face)
print("face_white extrema:", fw.getextrema(), "count>0:", sum(1 for v in fw.getdata() if v>0))
fb = Image.composite(zero, face, fw)
print("face_black extrema:", fb.getextrema(), "count>0:", sum(1 for v in fb.getdata() if v>0))
# final composite order test
img = Image.new("RGBA",(px,px),(0,0,0,0))
img.alpha_composite(M.paint(fb,(18,18,18)))
img.alpha_composite(M.paint(fw,(255,255,255)))
img.save(r"D:/Workspace/DeepSeek Harness/fn-fail2ban-upgrade/image/_dbg_face.png")
print("saved")