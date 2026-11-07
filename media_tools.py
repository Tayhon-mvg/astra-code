#!/usr/bin/env python3
"""
Astra Media Tools — медиа-обработка для Astra Code
Поддерживает: изображения (Pillow), видео (OpenCV), конвертация
"""

from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "Pillow"], check=True)
    from PIL import Image, ImageDraw, ImageFont

try:
    import cv2
    import numpy as np
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "opencv-python", "numpy"], check=True)
    import cv2
    import numpy as np


def create_image(
    width: int = 800,
    height: int = 600,
    color: str = "#ffffff",
    text: str = "",
    font_size: int = 48,
    output: str = "output.png",
) -> str:
    """Create or edit an image (PNG/JPG)."""
    img = Image.new("RGB", (width, height), color)
    draw = ImageDraw.Draw(img)

    if text:
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except (OSError, IOError):
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        x = (width - text_width) // 2
        y = (height - text_height) // 2

        draw.text((x, y), text, fill="black", font=font)

    img.save(output)
    return f"OK: image saved to {output}"


def resize_image(input_path: str, output_path: str, width: int, height: int) -> str:
    """Resize an image."""
    img = Image.open(input_path)
    resized = img.resize((width, height))
    resized.save(output_path)
    return f"OK: resized to {width}x{height} -> {output_path}"


def convert_image(input_path: str, output_path: str) -> str:
    """Convert image to another format."""
    img = Image.open(input_path)
    output_path = Path(output_path)
    if output_path.suffix.lower() in {".jpg", ".jpeg"}:
        img = img.convert("RGB")
    img.save(output_path)
    return f"OK: converted to {output_path.suffix.lower()} -> {output_path}"


def create_video(frames_dir: str, output_path: str, fps: int = 24) -> str:
    """Create video from frames directory."""
    frames = sorted(Path(frames_dir).glob("*.png")) or sorted(Path(frames_dir).glob("*.jpg"))

    if not frames:
        return "ERROR: no frames found"

    first = cv2.imread(str(frames[0]))
    h, w, _ = first.shape
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(output_path, fourcc, fps, (w, h))

    for frame_path in frames:
        frame = cv2.imread(str(frame_path))
        out.write(frame)

    out.release()
    return f"OK: video created with {len(frames)} frames -> {output_path}"


def extract_frames(video_path: str, output_dir: str, every_n: int = 1) -> str:
    """Extract frames from video."""
    cap = cv2.VideoCapture(video_path)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    count = 0
    saved = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if count % every_n == 0:
            out_file = output_path / f"frame_{saved:04d}.png"
            cv2.imwrite(str(out_file), frame)
            saved += 1

        count += 1

    cap.release()
    return f"OK: extracted {saved} frames -> {output_dir}"


def convert_video(input_path: str, output_path: str, format: str = "mp4") -> str:
    """Convert video using ffmpeg (requires ffmpeg in PATH)."""
    cmd = ["ffmpeg", "-y", "-i", input_path, output_path]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return f"OK: converted to {format} -> {output_path}"
    except FileNotFoundError:
        return "ERROR: ffmpeg not found. Install from https://ffmpeg.org/download.html"
    except subprocess.CalledProcessError as e:
        return f"ERROR: conversion failed: {e.stderr}"


def main():
    """CLI interface."""
    if len(sys.argv) < 2:
        print("Usage: astra_media_tools.py <command> [args...]")
        print("Commands:")
        print("  create-image --width W --height H --color COLOR --text TEXT --output FILE")
        print("  resize-image --input PATH --output PATH --width W --height H")
        print("  convert-image --input PATH --output PATH")
        print("  create-video --frames-dir DIR --output PATH [--fps N]")
        print("  extract-frames --video PATH --output-dir DIR [--every N]")
        print("  convert-video --input PATH --output PATH [--format mp4|avi]")
        return

    cmd = sys.argv[1]
    args = dict(zip(sys.argv[2::2], sys.argv[3::2]))

    if cmd == "create-image":
        result = create_image(
            width=int(args.get("--width", 800)),
            height=int(args.get("--height", 600)),
            color=args.get("--color", "#ffffff"),
            text=args.get("--text", ""),
            font_size=int(args.get("--font-size", 48)),
            output=args.get("--output", "output.png"),
        )
    elif cmd == "resize-image":
        result = resize_image(
            args.get("--input"),
            args.get("--output"),
            int(args.get("--width")),
            int(args.get("--height")),
        )
    elif cmd == "convert-image":
        result = convert_image(args.get("--input"), args.get("--output"))
    elif cmd == "create-video":
        result = create_video(
            args.get("--frames-dir"),
            args.get("--output"),
            fps=int(args.get("--fps", 24)),
        )
    elif cmd == "extract-frames":
        result = extract_frames(
            args.get("--video"),
            args.get("--output-dir"),
            every_n=int(args.get("--every", 1)),
        )
    elif cmd == "convert-video":
        result = convert_video(
            args.get("--input"),
            args.get("--output"),
            format=args.get("--format", "mp4"),
        )
    else:
        result = f"ERROR: unknown command {cmd}"

    print(result)


if __name__ == "__main__":
    main()
