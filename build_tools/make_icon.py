"""Generate assets/atlas.ico (an open book over a globe-like circle) with Pillow."""
from pathlib import Path
from PIL import Image, ImageDraw

def draw(size=256):
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((8, 8, size - 8, size - 8), 44, fill=(34, 42, 56, 255))
    c = size // 2
    d.ellipse((c - 78, c - 92, c + 78, c + 64), outline=(91, 155, 213, 255), width=8)
    d.arc((c - 40, c - 92, c + 40, c + 64), 90, 270, fill=(91, 155, 213, 255), width=5)
    d.arc((c - 40, c - 92, c + 40, c + 64), 270, 90, fill=(91, 155, 213, 255), width=5)
    d.line((c - 78, c - 14, c + 78, c - 14), fill=(91, 155, 213, 255), width=5)
    d.polygon([(c, c + 20), (c - 96, c + 4), (c - 96, c + 84), (c, c + 100)], fill=(236, 239, 244, 255))
    d.polygon([(c, c + 20), (c + 96, c + 4), (c + 96, c + 84), (c, c + 100)], fill=(214, 220, 230, 255))
    d.line((c, c + 20, c, c + 100), fill=(34, 42, 56, 255), width=5)
    return im

out = Path(__file__).resolve().parent.parent / "assets"
out.mkdir(exist_ok=True)
im = draw()
im.save(out / "atlas.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
im.save(out / "atlas.png")
print("icon written")
