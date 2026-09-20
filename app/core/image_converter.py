"""Bulk image converter — converts JPG/PNG/WEBP to any target format."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from PIL import Image

SUPPORTED_INPUT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif"}
SUPPORTED_OUTPUT = ["WEBP", "PNG", "JPG"]

ProgressCallback = Optional[Callable[[str], None]]


def _to_target_mode(img: Image.Image, target_format: str) -> Image.Image:
    """Convert image mode for the target format (flatten alpha for JPG/WEBP)."""
    if target_format == "PNG":
        if img.mode != "RGBA":
            return img.convert("RGBA")
        return img

    # JPG / WEBP — need RGB; paste onto white if transparent
    if img.mode in ("RGBA", "LA"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        alpha = img.split()[-1]
        background.paste(img.convert("RGBA"), mask=alpha)
        return background
    if img.mode == "P":
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        return background
    return img.convert("RGB")


def convert_images(
    input_paths: list[str],
    output_dir: str,
    target_format: str = "WEBP",
    quality: int = 85,
    max_width: int | None = None,
    progress: ProgressCallback = None,
) -> dict:
    """
    Convert a list of image files to target format.
    Returns: {converted: int, skipped: int, errors: list[str], output_files: list[str]}
    """
    target_format = (target_format or "WEBP").upper()
    if target_format not in SUPPORTED_OUTPUT:
        target_format = "WEBP"

    os.makedirs(output_dir, exist_ok=True)
    converted, skipped = 0, 0
    errors: list[str] = []
    output_files: list[str] = []

    for i, path in enumerate(input_paths):
        try:
            ext = Path(path).suffix.lower()
            if ext not in SUPPORTED_INPUT:
                skipped += 1
                continue

            with Image.open(path) as opened:
                img = _to_target_mode(opened, target_format)

                if max_width and img.width > max_width:
                    ratio = max_width / img.width
                    img = img.resize(
                        (max_width, int(img.height * ratio)),
                        Image.Resampling.LANCZOS,
                    )

                stem = Path(path).stem
                out_ext = ".jpg" if target_format == "JPG" else f".{target_format.lower()}"
                out_path = os.path.join(output_dir, stem + out_ext)

                save_kwargs: dict = {"quality": quality}
                if target_format == "WEBP":
                    save_kwargs["method"] = 6
                    save_kwargs["optimize"] = True
                elif target_format == "PNG":
                    save_kwargs = {"optimize": True}
                elif target_format == "JPG":
                    save_kwargs["optimize"] = True
                    save_kwargs["quality"] = quality

                pil_format = "JPEG" if target_format == "JPG" else target_format
                img.save(out_path, format=pil_format, **save_kwargs)

            output_files.append(out_path)
            converted += 1

            if progress:
                progress(f"Converted {i + 1}/{len(input_paths)}: {Path(path).name}")

        except Exception as e:
            errors.append(f"{Path(path).name}: {e}")

    return {
        "converted": converted,
        "skipped": skipped,
        "errors": errors,
        "output_files": output_files,
    }


def collect_images_from_folder(folder: str) -> list[str]:
    """Recursively collect supported image paths under folder."""
    root = Path(folder)
    if not root.is_dir():
        return []
    found: list[str] = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in SUPPORTED_INPUT:
            found.append(str(p))
    return sorted(found)
