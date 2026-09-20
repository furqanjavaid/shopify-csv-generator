"""Shopify Product Tools — desktop entry point.

Screens: HomeScreen, UploadScreen, ScraperScreen, MappingScreen,
SuccessScreen, AuditScreen, SettingsScreen.
"""

from __future__ import annotations

import customtkinter as ctk

from app.ui import theme as T
from app.ui.audit_screen import AuditScreen  # noqa: F401
from app.ui.home_screen import HomeScreen
from app.utils.config import get_theme


class App(ctk.CTk):
    """Main application window with screen navigation."""

    def __init__(self) -> None:
        super().__init__()

        self.title("Shopify Product Tools — Sentivo")
        self.geometry("980x700")
        self.minsize(960, 640)
        self.resizable(True, True)
        self.configure(fg_color=T.BG)

        self._center_window()

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


def main() -> None:
    theme = get_theme()
    T.apply_theme(theme)
    ctk.set_appearance_mode("dark" if theme == "dark" else "light")
    ctk.set_default_color_theme("dark-blue")
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
