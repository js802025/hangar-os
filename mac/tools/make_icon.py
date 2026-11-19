"""Hangar for Mac's app icon: the ✦ on Hangar's night-blue, as a 1024px PNG."""
import sys
from PIL import Image, ImageDraw, ImageFilter

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
# rounded square, macOS-style margins
m, r = 100, 185
bg = Image.new("RGBA", (S, S))
g = ImageDraw.Draw(bg)
for y in range(S):      # vertical gradient
    t = y / S
    g.line([(0, y), (S, y)], fill=(int(22 + 10 * t), int(27 + 12 * t), int(44 + 30 * t), 255))
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([m, m, S - m, S - m], r, fill=255)
img.paste(bg, (0, 0), mask)
# soft glow + star: four points joined by inward curves, like ✦
def star(cx, cy, R, k=0.18, n=24):
    tips = [(cx, cy - R), (cx + R, cy), (cx, cy + R), (cx - R, cy)]
    pts = []
    for i in range(4):
        (x0, y0), (x1, y1) = tips[i], tips[(i + 1) % 4]
        qx, qy = cx + (x0 + x1 - 2 * cx) * k, cy + (y0 + y1 - 2 * cy) * k   # pull toward the centre
        for j in range(n):
            t = j / n
            pts.append(((1 - t) ** 2 * x0 + 2 * (1 - t) * t * qx + t * t * x1,
                        (1 - t) ** 2 * y0 + 2 * (1 - t) * t * qy + t * t * y1))
    return pts

glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(glow).polygon(star(S / 2, S / 2, 300), fill=(124, 156, 255, 210))
img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(45)))
ImageDraw.Draw(img).polygon(star(S / 2, S / 2, 270), fill=(220, 228, 255, 255))
img.save(sys.argv[1])
