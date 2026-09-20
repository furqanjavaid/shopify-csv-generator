"""Sentivo Tools — desktop entry point.

Screens: HomeScreen, UploadScreen, ScraperScreen, MappingScreen,
SuccessScreen, AuditScreen, ConverterScreen, SettingsScreen.
"""

from __future__ import annotations

import threading

import customtkinter as ctk

from app.ui import theme as T
from app.ui.audit_screen import AuditScreen  # noqa: F401
from app.ui.converter_screen import ConverterScreen  # noqa: F401
from app.ui.home_screen import HomeScreen
from app.utils.config import get_theme
from app.utils.updater import check_for_update, download_and_install, get_current_version


class App(ctk.CTk):
    """Main application window with screen navigation."""

    def __init__(self) -> None:
        super().__init__()

        self.title(f"Sentivo Tools  v{get_current_version()}")
        self.geometry("980x700")
        self.minsize(960, 640)
        self.resizable(True, True)
        self.configure(fg_color=T.BG)

        self._center_window()
        self._update_banner = None

        self.container = ctk.CTkFrame(self, fg_color=T.BG, corner_radius=0)
        self.container.pack(fill="both", expand=True)

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
    theme = get_theme()
    T.apply_theme(theme)
    ctk.set_appearance_mode("dark" if theme == "dark" else "light")
    ctk.set_default_color_theme("dark-blue")
    app = App()

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
