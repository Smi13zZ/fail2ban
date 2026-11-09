import sys
sys.path.insert(0, r"D:/Workspace/DeepSeek Harness/fn-fail2ban-upgrade/tools")
import make_icons as M
from PIL import Image, ImageDraw
px = 400
cx = px/2; half_w = px*0.300; height = px*0.590; top = px*0.180; cy = top + height*0.50
outer = M.shield_path(cx, top, half_w, height)
inner = M.scale_about(outer, cx, cy, 0.80)
face = Image.new("L",(px,px),0); ImageDraw.Draw(face).polygon(inner, fill=255)
slash = Image.new("L",(px,px),0); sd = ImageDraw.Draw(slash)
band = px*0.132
for s in (px*0.690, px*0.690 + band*2.0):
    sd.polygon([(s,0),(s+band,0),(0,s+band),(0,s)], fill=255)
fw = Image.composite(slash, Image.new("L",(px,px),0), face)
fb = Image.composite(Image.new("L",(px,px),0), face, fw)
print("face px:", sum(1 for v in face.get_flattened_data() if v>0))
print("white px:", sum(1 for v in fw.get_flattened_data() if v>0))
print("black px:", sum(1 for v in fb.get_flattened_data() if v>0))
rim = Image.new("L",(px,px),0); rd=ImageDraw.Draw(rim)
rd.polygon(outer, fill=255); rd.polygon(inner, fill=0)
print("rim px:", sum(1 for v in rim.get_flattened_data() if v>0))
# does white ever escape the face? should be exactly 0
escape = sum(1 for a,b in zip(fw.get_flattened_data(), face.get_flattened_data()) if a>0 and b==0)
print("white OUTSIDE face:", escape)