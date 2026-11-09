import sys, math
sys.path.insert(0, r"D:/Workspace/DeepSeek Harness/fn-fail2ban-upgrade/tools")
from PIL import Image, ImageDraw
px = 400
strip = Image.new("RGB", (px*3, px), (255,255,255))
for i, amp in enumerate((0.16, 0.24, 0.32)):
    cx = px/2; half_w = px*0.300; height = px*0.590; top = px*0.195; cy = top + height*0.50
    right = []
    for j in range(241):
        u = j/240
        y = top + u*height
        if u < 0.26:
            k = u/0.26; hw = half_w*(0.950 + 0.050*math.sin(k*math.pi/2))
        else:
            k = max(0.0, min(1.0,(u-0.26)/0.74)); hw = half_w*(max(0.0, math.cos(k*math.pi/2))**0.58)
        right.append((cx+hw, y))
    # crown: lift the whole top edge with a raised-cosine so the CENTRE is highest
    crown = []
    for (x, y) in right:
        tnorm = (x - (cx - half_w)) / (2*half_w)
        tnorm = max(0.0, min(1.0, tnorm))
        lift = half_w*amp*(0.5*(1.0 + math.cos(math.pi*(2*tnorm-1))))
        crown.append((x, y - lift))
    left = [(2*cx-x,y) for (x,y) in crown]
    outer = crown + left[::-1][1:-1]
    im = Image.new("RGB",(px,px),(255,255,255))
    ImageDraw.Draw(im).polygon(outer, fill=(18,18,18))
    strip.paste(im, (px*i, 0))
strip.save(r"D:/Workspace/DeepSeek Harness/fn-fail2ban-upgrade/image/_dbg_arch2.png")
print("saved")