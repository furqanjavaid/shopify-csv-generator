"""CLI entry for bulk image conversion (used by Electron)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sentivo image converter")
    parser.add_argument("--input", required=True, help="Input folder of images")
    parser.add_argument("--output", required=True, help="Output folder")
    parser.add_argument(
        "--format",
        default="WEBP",
        choices=["WEBP", "PNG", "JPG", "webp", "png", "jpg", "Jpeg", "JPEG"],
        help="Target format",
    )
    parser.add_argument("--quality", default="85", help="Quality 1-100")
    args = parser.parse_args(argv)

    from app.core.image_converter import collect_images_from_folder, convert_images

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    if not input_dir.is_dir():
        print(f"[ERROR] Input folder not found: {input_dir}")
        return 2

    paths = collect_images_from_folder(str(input_dir))
    total = len(paths)
    print(f"[INFO] Found {total} image(s) in {input_dir}")
    if total == 0:
        print("[WARNING] No supported images found.")
        return 1

    fmt = str(args.format or "WEBP").upper()
    if fmt == "JPEG":
        fmt = "JPG"
    try:
        quality = max(1, min(100, int(args.quality)))
    except ValueError:
        quality = 85

    def progress(msg: str) -> None:
        # Converted 3/10: file.jpg
        print(f"[INFO] {msg}")
        if "/" in msg:
            try:
                part = msg.split(":", 1)[0]
                cur_s, tot_s = part.replace("Converted", "").strip().split("/")
                cur, tot = int(cur_s), int(tot_s)
                if tot > 0:
                    pct = int(cur * 100 / tot)
                    print(f"[INFO] progress={pct}%")
            except Exception:
                pass

    print(f"[INFO] Converting to {fmt} at quality {quality}…")
    result = convert_images(
        paths,
        str(output_dir),
        target_format=fmt,
        quality=quality,
        progress=progress,
    )
    print(
        f"[INFO] Done. converted={result.get('converted', 0)} "
        f"skipped={result.get('skipped', 0)} "
        f"errors={len(result.get('errors') or [])}"
    )
    for err in result.get("errors") or []:
        print(f"[ERROR] {err}")
    print(f"[INFO] Output Path: {output_dir.resolve()}")
    return 0 if result.get("converted", 0) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
