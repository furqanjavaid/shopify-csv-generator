"""Build standalone executable with PyInstaller."""

from __future__ import annotations

import subprocess
import sys


def main() -> int:
    sep = ";" if sys.platform.startswith("win") else ":"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--windowed",
        "--name",
        "ShopifyCSVGenerator",
        "--add-data",
        f"app{sep}app",
        "--hidden-import",
        "customtkinter",
        "--hidden-import",
        "pandas",
        "--hidden-import",
        "openpyxl",
        "--hidden-import",
        "bs4",
        "--hidden-import",
        "lxml",
        "main.py",
    ]

    print(f"Building for platform: {sys.platform}")
    print("Running:", " ".join(cmd))
    print()

    result = subprocess.run(cmd, check=False)
    if result.returncode == 0:
        print()
        print("✓ Build succeeded!")
        if sys.platform.startswith("win"):
            print("  Output: dist/ShopifyCSVGenerator.exe")
        else:
            print("  Output: dist/ShopifyCSVGenerator")
        return 0

    print()
    print("✗ Build failed. Check the PyInstaller output above.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
