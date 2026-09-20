"""Build script — creates SentivoTools.exe"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def clean():
    for folder in ["build", "dist"]:
        path = os.path.join(ROOT, folder)
        if os.path.exists(path):
            shutil.rmtree(path)
            print(f"Cleaned: {folder}/")


def build():
    print("Building SentivoTools.exe...")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onefile",
            "--windowed",
            "--noconsole",
            "--name",
            "SentivoTools",
            "--add-data",
            "app;app",
            "--add-data",
            "version.json;.",
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
            "--hidden-import",
            "PIL",
            "--hidden-import",
            "PIL.Image",
            "--hidden-import",
            "docx",
            "--hidden-import",
            "requests",
            "--hidden-import",
            "playwright",
            "--collect-all",
            "customtkinter",
            "--collect-all",
            "playwright",
            "main.py",
        ],
        cwd=ROOT,
    )

    if result.returncode == 0:
        exe_path = os.path.join(ROOT, "dist", "SentivoTools.exe")
        size = os.path.getsize(exe_path) / (1024 * 1024)
        print("\n[OK] Build successful!")
        print(f"Output: {exe_path}")
        print(f"Size: {size:.1f} MB")
    else:
        print("\n[FAIL] Build failed!")
        sys.exit(1)


if __name__ == "__main__":
    clean()
    build()
