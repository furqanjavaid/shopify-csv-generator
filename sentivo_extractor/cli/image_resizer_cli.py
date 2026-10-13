"""
Bulk image resizer CLI for Sentivo Tools.

Reads one JSON object from stdin and streams JSON progress lines to stdout:
  {"type": "progress", "current": 1, "total": 10, "file": "image.jpg", "size": "800x600", "kb": 45}
  {"type": "error", "file": "image.jpg", "error": "message"}
  {"type": "done", "total": 10, "output_folder": "C:/path/to/output"}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _read_request() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("No JSON payload on stdin.")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Payload must be a JSON object.")
    return data


def _collect_files(files: list[Any]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for item in files or []:
        p = Path(str(item or "").strip())
        if not p.is_file():
            continue
        if p.suffix.lower() not in _IMAGE_EXTS:
            continue
        key = str(p.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _calc_size(
    orig_w: int,
    orig_h: int,
    *,
    mode: str,
    width: int,
    height: int,
    percentage: float,
    max_side: int,
    maintain_aspect: bool,
) -> tuple[int, int]:
    if orig_w <= 0 or orig_h <= 0:
        return orig_w, orig_h

    mode = (mode or "dimensions").strip().lower()
    if mode == "percentage":
        pct = max(1.0, float(percentage or 100)) / 100.0
        return max(1, int(round(orig_w * pct))), max(1, int(round(orig_h * pct)))

    if mode == "maxside":
        side = max(1, int(max_side or 1))
        longest = max(orig_w, orig_h)
        if longest <= side:
            return orig_w, orig_h
        scale = side / float(longest)
        return max(1, int(round(orig_w * scale))), max(1, int(round(orig_h * scale)))

    # dimensions
    tw = max(0, int(width or 0))
    th = max(0, int(height or 0))
    if tw <= 0 and th <= 0:
        return orig_w, orig_h
    if maintain_aspect:
        if tw > 0 and th > 0:
            scale = min(tw / float(orig_w), th / float(orig_h))
            return max(1, int(round(orig_w * scale))), max(1, int(round(orig_h * scale)))
        if tw > 0:
            scale = tw / float(orig_w)
            return max(1, tw), max(1, int(round(orig_h * scale)))
        scale = th / float(orig_h)
        return max(1, int(round(orig_w * scale))), max(1, th)

    return max(1, tw or orig_w), max(1, th or orig_h)


def _target_format(path: Path, output_format: str) -> tuple[str, str]:
    """Return (pillow_format, extension_with_dot)."""
    fmt = (output_format or "same").strip().lower()
    if fmt in {"", "same", "original"}:
        ext = path.suffix.lower()
        if ext in {".jpg", ".jpeg"}:
            return "JPEG", ".jpg"
        if ext == ".png":
            return "PNG", ".png"
        if ext == ".webp":
            return "WEBP", ".webp"
        if ext == ".gif":
            return "PNG", ".png"  # save static frame as PNG
        return "PNG", ".png"
    if fmt in {"jpg", "jpeg"}:
        return "JPEG", ".jpg"
    if fmt == "png":
        return "PNG", ".png"
    if fmt == "webp":
        return "WEBP", ".webp"
    return "PNG", ".png"


def _prepare_for_save(img: Any, pillow_format: str) -> Any:
    from PIL import Image

    if pillow_format == "JPEG":
        if img.mode in ("RGBA", "LA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            alpha = img.split()[-1]
            background.paste(img.convert("RGBA"), mask=alpha)
            return background
        if img.mode != "RGB":
            return img.convert("RGB")
        return img
    if pillow_format == "WEBP":
        if img.mode not in ("RGB", "RGBA"):
            return img.convert("RGBA")
        return img
    if pillow_format == "PNG":
        if img.mode not in ("RGB", "RGBA", "L", "LA", "P"):
            return img.convert("RGBA")
        return img
    return img


def _resize_one(
    src: Path,
    output_folder: Path,
    *,
    mode: str,
    width: int,
    height: int,
    percentage: float,
    max_side: int,
    maintain_aspect: bool,
    output_format: str,
    quality: int,
) -> dict[str, Any]:
    from PIL import Image

    with Image.open(src) as im:
        im.load()
        # Use first frame for animated GIF/WebP
        if getattr(im, "is_animated", False):
            im.seek(0)
        frame = im.convert("RGBA") if im.mode in ("P", "LA") else im.copy()
        new_w, new_h = _calc_size(
            frame.width,
            frame.height,
            mode=mode,
            width=width,
            height=height,
            percentage=percentage,
            max_side=max_side,
            maintain_aspect=maintain_aspect,
        )
        if (new_w, new_h) != (frame.width, frame.height):
            frame = frame.resize((new_w, new_h), Image.Resampling.LANCZOS)

        pillow_format, ext = _target_format(src, output_format)
        out_name = src.stem + ext
        dest = output_folder / out_name
        # Avoid overwrite collisions within same run
        if dest.exists():
            n = 1
            while True:
                candidate = output_folder / f"{src.stem}_{n}{ext}"
                if not candidate.exists():
                    dest = candidate
                    break
                n += 1

        to_save = _prepare_for_save(frame, pillow_format)
        save_kwargs: dict[str, Any] = {}
        if pillow_format in {"JPEG", "WEBP"}:
            save_kwargs["quality"] = max(1, min(100, int(quality)))
            if pillow_format == "JPEG":
                save_kwargs["optimize"] = True
            if pillow_format == "WEBP":
                save_kwargs["method"] = 4
        to_save.save(dest, format=pillow_format, **save_kwargs)
        kb = max(1, int(round(dest.stat().st_size / 1024.0)))
        return {
            "file": src.name,
            "size": f"{new_w}x{new_h}",
            "kb": kb,
            "output": str(dest),
        }


def main() -> int:
    try:
        payload = _read_request()
    except Exception as exc:  # noqa: BLE001
        _emit({"type": "error", "file": "", "error": f"Invalid JSON: {exc}"})
        return 2

    files = _collect_files(payload.get("files") or [])
    mode = str(payload.get("mode") or "dimensions").strip().lower()
    width = int(payload.get("width") or 0)
    height = int(payload.get("height") or 0)
    percentage = float(payload.get("percentage") or 100)
    max_side = int(payload.get("max_side") or payload.get("maxSide") or 0)
    maintain_aspect = bool(payload.get("maintain_aspect", payload.get("maintainAspect", True)))
    output_format = str(payload.get("output_format") or payload.get("outputFormat") or "same")
    try:
        quality = max(1, min(100, int(payload.get("quality") or 85)))
    except (TypeError, ValueError):
        quality = 85
    output_folder = Path(
        str(payload.get("output_folder") or payload.get("outputFolder") or "").strip()
    )

    if not files:
        _emit({"type": "error", "file": "", "error": "No valid image files provided."})
        return 2
    if not str(output_folder):
        _emit({"type": "error", "file": "", "error": "Output folder is required."})
        return 2

    try:
        output_folder.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        _emit({"type": "error", "file": "", "error": f"Cannot create output folder: {exc}"})
        return 2

    total = len(files)
    ok_count = 0
    for i, src in enumerate(files, start=1):
        try:
            result = _resize_one(
                src,
                output_folder,
                mode=mode,
                width=width,
                height=height,
                percentage=percentage,
                max_side=max_side,
                maintain_aspect=maintain_aspect,
                output_format=output_format,
                quality=quality,
            )
            ok_count += 1
            _emit(
                {
                    "type": "progress",
                    "current": i,
                    "total": total,
                    "file": result["file"],
                    "size": result["size"],
                    "kb": result["kb"],
                }
            )
        except Exception as exc:  # noqa: BLE001
            _emit({"type": "error", "file": src.name, "error": str(exc)})
            _emit(
                {
                    "type": "progress",
                    "current": i,
                    "total": total,
                    "file": src.name,
                    "size": "",
                    "kb": 0,
                    "failed": True,
                }
            )

    _emit(
        {
            "type": "done",
            "total": total,
            "ok": ok_count,
            "output_folder": str(output_folder.resolve()),
        }
    )
    return 0 if ok_count else 1


if __name__ == "__main__":
    raise SystemExit(main())
