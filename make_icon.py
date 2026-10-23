"""
make_icon.py
Generates all required app icon formats.
Run once before building: python make_icon.py

Outputs:
  assets/icon.png   (512x512, Linux + general use)
  assets/icon.ico   (multi-res, Windows)
  assets/icon.icns  (macOS — only generated on macOS using iconutil)
"""

import math
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Pillow not found. Installing...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pillow"])
    from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).parent / "assets"
ASSETS.mkdir(exist_ok=True)
SIZE = 512


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    s, pad = size, size * 0.08

    # Background circle — dark navy
    draw.ellipse([pad, pad, s - pad, s - pad], fill=(18, 42, 66, 255))

    # Two sinusoidal DNA strands
    steps = 120
    strand_a, strand_b = [], []
    for i in range(steps + 1):
        t = i / steps
        x = pad * 1.8 + t * (s - pad * 3.6)
        amp = s * 0.17
        y_a = s / 2 + math.sin(t * math.pi * 4) * amp
        y_b = s / 2 - math.sin(t * math.pi * 4) * amp
        strand_a.append((x, y_a))
        strand_b.append((x, y_b))

    lw = max(2, size // 52)

    # Connecting rungs
    for i in range(0, steps + 1, 7):
        draw.line([strand_a[i], strand_b[i]], fill=(55, 138, 221, 130), width=lw)

    # Strands
    for i in range(steps):
        draw.line([strand_a[i], strand_a[i+1]], fill=(79, 168, 79, 255),  width=lw * 2)
        draw.line([strand_b[i], strand_b[i+1]], fill=(55, 138, 221, 255), width=lw * 2)

    return img


def make_png() -> Image.Image:
    img = draw_icon(SIZE)
    path = ASSETS / "icon.png"
    img.save(path, "PNG")
    print(f"  ✓ {path}")
    return img


def make_ico(base: Image.Image):
    sizes = [16, 32, 48, 64, 128, 256]
    imgs  = [base.resize((s, s), Image.LANCZOS) for s in sizes]
    path  = ASSETS / "icon.ico"
    imgs[0].save(path, format="ICO", sizes=[(s,s) for s in sizes], append_images=imgs[1:])
    print(f"  ✓ {path}")


def make_icns(base: Image.Image):
    if platform.system() != "Darwin":
        print("  (skipping .icns — only built on macOS)")
        return
    if not shutil.which("iconutil"):
        print("  (skipping .icns — iconutil not found)")
        return

    specs = [
        (16,   "icon_16x16.png"),
        (32,   "icon_16x16@2x.png"),
        (32,   "icon_32x32.png"),
        (64,   "icon_32x32@2x.png"),
        (128,  "icon_128x128.png"),
        (256,  "icon_128x128@2x.png"),
        (256,  "icon_256x256.png"),
        (512,  "icon_256x256@2x.png"),
        (512,  "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        for res, name in specs:
            base.resize((res, res), Image.LANCZOS).save(iconset / name)
        out = ASSETS / "icon.icns"
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
    print(f"  ✓ {out}")


if __name__ == "__main__":
    print("Generating icons...")
    img = make_png()
    make_ico(img)
    make_icns(img)
    print("Done.")
