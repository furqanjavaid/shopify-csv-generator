"""Sentivo Tools — desktop entry point.

Screens: HomeScreen, UploadScreen, ScraperScreen, MappingScreen,
SuccessScreen, AuditScreen, ConverterScreen, SettingsScreen.

Also supports Universal Product Extractor CLI when --input is passed:
  python main.py --input input/websites.csv --output output/ --use-playwright true
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path


def _wants_extractor_cli(argv: list[str]) -> bool:
    """Route to sentivo_extractor when CLI flags / subcommands are present."""
    if len(argv) > 1 and argv[1] in ("audit", "extract"):
        return True
    flags = {"--input", "--output", "--download-images", "--use-playwright"}
    return any(a == "--input" or a.startswith("--input=") for a in argv) or (
        len(argv) > 1 and any(a.split("=")[0] in flags for a in argv[1:])
    )


if __name__ == "__main__" and _wants_extractor_cli(sys.argv):
    from sentivo_extractor.cli import run_cli

    raise SystemExit(run_cli(sys.argv[1:]))

import customtkinter as ctk

from app.ui import theme as T
from app.ui.audit_screen import AuditScreen  # noqa: F401
from app.ui.converter_screen import ConverterScreen  # noqa: F401
from app.ui.home_screen import HomeScreen
from app.utils.updater import check_for_update, download_and_install, get_current_version

PROJECT_ROOT = Path(__file__).resolve().parent
ASSETS_DIR = PROJECT_ROOT / "assets"
ICON_ICO = ASSETS_DIR / "icon.ico"
ICON_PNG = ASSETS_DIR / "sentivo icon.png"


def _apply_window_icon(app: ctk.CTk) -> None:
    """Set taskbar / window icon from assets."""
    try:
        if ICON_ICO.exists():
            app.iconbitmap(default=str(ICON_ICO))
            app.iconbitmap(str(ICON_ICO))
    except Exception:
        pass
    try:
        if ICON_PNG.exists():
            from PIL import Image, ImageTk

            img = Image.open(ICON_PNG)
            photo = ImageTk.PhotoImage(img)
            app.iconphoto(True, photo)
            app._icon_photo = photo  # prevent GC
    except Exception:
        pass


class App(ctk.CTk):
    """Main application window with screen navigation."""

    def __init__(self) -> None:
        super().__init__()

        self.title(f"Sentivo Tools  v{get_current_version()}")
        self.geometry("980x700")
        self.minsize(960, 640)
        self.resizable(True, True)
        self.configure(fg_color=T.get("BG_PRIMARY"))
        _apply_window_icon(self)

        self._center_window()
        self._update_banner = None

        self.container = ctk.CTkFrame(self, fg_color=T.get("BG_PRIMARY"), corner_radius=0)
        self.container.pack(fill="both", expand=True)

        from app.utils.job_status import JobStatusStore

        self.job_status = JobStatusStore()

        self.current_screen = None
        self.show_screen(HomeScreen)

    def _center_window(self) -> None:
        self.update_idletasks()
        width, height = 980, 700
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = (screen_w - width) // 2
        y = (screen_h - height) // 2
        self.geometry(f"{width}x{height}+{x}+{y}")

    def show_screen(self, screen_class, **kwargs) -> None:
        """Destroy the current screen and show a new one."""
        if self.current_screen is not None:
            self.current_screen.destroy()
            self.current_screen = None

        self.current_screen = screen_class(self.container, app=self, **kwargs)
        self.current_screen.pack(fill="both", expand=True)

    def show_update_banner(
        self, latest_version: str, download_url: str | None, release_notes: str
    ) -> None:
        """Show update banner at top of window."""
        if self._update_banner is not None:
            try:
                self._update_banner.destroy()
            except Exception:
                pass

        notes = (release_notes or "Bug fixes and improvements").replace("\n", " ")
        banner = ctk.CTkFrame(self, fg_color="#1a3a1a", height=44, corner_radius=0)
        banner.pack(fill="x", side="top", before=self.container)
        banner.pack_propagate(False)
        self._update_banner = banner

        inner = ctk.CTkFrame(banner, fg_color="transparent")
        inner.pack(expand=True)

        ctk.CTkLabel(
            inner,
            text=f"🔔 Update v{latest_version} available — {notes[:60]}",
            font=T.font(12),
            text_color="#90EE90",
        ).pack(side="left", padx=(0, 16))

        if download_url:
            update_btn = ctk.CTkButton(
                inner,
                text="Update Now",
                fg_color="#2d5a2d",
                hover_color="#3a7a3a",
                text_color="white",
                height=28,
                width=100,
                font=T.font(12, "bold"),
                corner_radius=4,
            )

            def _do_update() -> None:
                update_btn.configure(text="Downloading...", state="disabled")

                def _progress(pct: int) -> None:
                    self.after(
                        0,
                        lambda p=pct: update_btn.configure(text=f"Downloading {p}%..."),
                    )

                threading.Thread(
                    target=lambda: download_and_install(download_url, _progress),
                    daemon=True,
                ).start()

            update_btn.configure(command=_do_update)
            update_btn.pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            inner,
            text="✕",
            command=banner.destroy,
            fg_color="transparent",
            hover_color="#2d5a2d",
            text_color="#90EE90",
            height=28,
            width=28,
            font=T.font(12),
            corner_radius=4,
        ).pack(side="left")


def main() -> None:
    # Mockups are light-only — force light and persist so inputs stay readable.
    try:
        from app.utils.config import set_theme

        set_theme("light")
    except Exception:
        pass
    T.apply_theme("light")
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    app = App()
    try:
        T.apply_thin_scrollbars(app)
    except Exception:
        pass

    def _on_update_available(latest_version, download_url, release_notes):
        app.after(
            0,
            lambda: app.show_update_banner(
                latest_version, download_url, release_notes
            ),
        )

    check_for_update(_on_update_available)
    app.mainloop()


if __name__ == "__main__":
    main()
