"""Shopify CSV Generator — desktop entry point.

Screens: HomeScreen, UploadScreen, ScraperScreen, MappingScreen,
SuccessScreen, AuditScreen (Shopify store CRO/SEO auditor).
"""

from __future__ import annotations

import customtkinter as ctk

from app.ui.audit_screen import AuditScreen  # noqa: F401 — registered via navigation
from app.ui.home_screen import HomeScreen


class App(ctk.CTk):
    """Main application window with screen navigation."""

    def __init__(self) -> None:
        super().__init__()

        self.title("Shopify CSV Generator")
        self.geometry("900x650")
        self.resizable(False, False)
        self.configure(fg_color="#1a1a2e")

        self._center_window()

        self.container = ctk.CTkFrame(self, fg_color="#1a1a2e", corner_radius=0)
        self.container.pack(fill="both", expand=True)

        self.current_screen = None
        self.show_screen(HomeScreen)

    def _center_window(self) -> None:
        self.update_idletasks()
        width, height = 900, 650
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
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
