"""Capture docs/demo.html with headless Edge and crop the PNG to content."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "docs" / "demo.html"
PNG = ROOT / "docs" / "demo.png"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def crop() -> None:
    image = Image.open(PNG).convert("RGB")
    background = Image.new("RGB", image.size, image.getpixel((0, 0)))
    diff = ImageChops.difference(image, background).convert("L")
    mask = diff.point(lambda value: 255 if value > 10 else 0)
    box = mask.getbbox()
    if not box:
        return
    padding = 28
    left = max(0, box[0] - padding)
    top = max(0, box[1] - padding)
    right = min(image.width, box[2] + padding)
    bottom = min(image.height, box[3] + padding)
    image.crop((left, top, right, bottom)).save(PNG)
    print(f"cropped {PNG} -> {right - left}x{bottom - top}")


def main() -> int:
    if not EDGE.is_file():
        print(f"Edge not found: {EDGE}", file=sys.stderr)
        return 1
    uri = HTML.resolve().as_uri()
    command = [
        str(EDGE),
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--window-size=1020,950",
        f"--screenshot={PNG}",
        uri,
    ]
    subprocess.run(command, check=False, capture_output=True)
    crop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
