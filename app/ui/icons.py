"""Load Lucide-style outline icons from assets/icons as CTkImage."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import customtkinter as ctk
from PIL import Image

from app.ui import theme as T
from app.utils.helpers import resource_path

ICONS_DIR = Path(resource_path("assets/icons"))

# White outlines for charcoal sidebar; charcoal/orange for light content surfaces.
_COLOR_WHITE = (255, 255, 255, 255)
_COLOR_NAVY = (28, 28, 30, 255)  # charcoal #1C1C1E (legacy "navy" token)
_COLOR_MUTED = (156, 163, 175, 255)  # #9CA3AF
_COLOR_BURGUNDY = (232, 89, 12, 255)  # orange #E8590C (legacy accent token)
_COLOR_GOLD = (232, 89, 12, 255)
_COLOR_ORANGE = (232, 89, 12, 255)


def _recolor(img: Image.Image, rgba: tuple[int, int, int, int]) -> Image.Image:
    """Replace non-transparent pixels with the given RGBA color (keeps alpha)."""
    src = img.convert("RGBA")
    pixels = src.load()
    w, h = src.size
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dest = out.load()
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            if a < 16:
                continue
            dest[x, y] = (*rgba[:3], a)
    return out


@lru_cache(maxsize=128)
def load_icon(
    name: str,
    size: int = 18,
    *,
    color: str = "white",
) -> ctk.CTkImage | None:
    """
    Load assets/icons/<name>.png tinted for UI use.
    color: white | navy | muted | burgundy | gold
    """
    path = ICONS_DIR / f"{name}.png"
    if not path.exists():
        return None
    try:
        base = Image.open(path).convert("RGBA")
    except Exception:
        return None

    palette = {
        "white": _COLOR_WHITE,
        "navy": _COLOR_NAVY,
        "charcoal": _COLOR_NAVY,
        "muted": _COLOR_MUTED,
        "burgundy": _COLOR_BURGUNDY,
        "gold": _COLOR_GOLD,
        "orange": _COLOR_ORANGE,
    }
    tinted = _recolor(base, palette.get(color, _COLOR_NAVY))
    tinted = tinted.resize((size, size), Image.Resampling.LANCZOS)
    return ctk.CTkImage(light_image=tinted, dark_image=tinted, size=(size, size))


def icon_names() -> list[str]:
    return sorted(p.stem for p in ICONS_DIR.glob("*.png"))
