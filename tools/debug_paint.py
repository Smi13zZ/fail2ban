import sys, os
sys.path.insert(0, r"D:/Workspace/DeepSeek Harness/fn-fail2ban-upgrade/tools")
import importlib, make_icons as M
importlib.reload(M)
from PIL import Image
print("module file:", M.__file__)
master = M.build_master()
print("mode:", master.mode, "size:", master.size)
px = 400
import math
cx = px / 2; half_w = px*0.300; height = px*0.590; top = px*0.180; cy = top + height*0.50
outer = M.shield_path(cx, top, half_w, height)
inner = M.scale_about(outer, cx, cy, 0.80)
rim = Image.new("L",(px,px),0)
from PIL import ImageDraw
rd=ImageDraw.Draw(rim); rd.polygon(outer,fill=255); rd.polygon(inner,fill=0)
print("rim extrema:", rim.getextrema())
lay = M.paint(rim, (18,18,18))
print("layer mode:", lay.mode, "alpha extrema:", lay.getchannel("A").getextrema())
base_im = Image.new("RGBA",(px,px),(0,0,0,0))
base_im.alpha_composite(lay)
print("after composite alpha extrema:", base_im.getchannel("A").getextrema())
base_im.save(r"D:/Workspace/DeepSeek Harness/fn-fail2ban-upgrade/image/_debug_paint.png")