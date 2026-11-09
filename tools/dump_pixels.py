from PIL import Image
p = r"D:/Workspace/DeepSeek Harness/fn-fail2ban-upgrade/src/pkg/fn-fail2ban/app/ui/images/icon_256.png"
im = Image.open(p).convert("RGB")
w, h = im.size
# sample a grid of pixels to see what is actually in the file
for y in (30, 60, 100, 140, 180, 220):
    row = []
    for x in (40, 70, 100, 128, 156, 186, 216):
        r, g, b = im.getpixel((x, y))
        row.append("K" if r + g + b < 200 else ("W" if r + g + b > 700 else "?"))
    print(y, " ".join(row))
print("---")
print("has black:", any(sum(im.getpixel((x,y))) < 200 for x in range(0,256,8) for y in range(0,256,8)))