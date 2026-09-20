"""App preference config (theme, etc.)."""

from __future__ import annotations

import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "../../config.json")


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"theme": "dark"}


def save_config(data: dict) -> None:
    path = os.path.abspath(CONFIG_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_theme() -> str:
    theme = load_config().get("theme", "dark")
    return "light" if str(theme).lower() == "light" else "dark"


def set_theme(value: str) -> None:
    cfg = load_config()
    cfg["theme"] = "light" if str(value).lower() == "light" else "dark"
    save_config(cfg)
