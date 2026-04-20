"""Package entry point for the LCD screen service."""

from __future__ import annotations

import os


def _bootstrap_django() -> None:
    """Ensure Django is configured before importing the LCD runner."""

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    import django

    django.setup()


def _run_lcd_service() -> None:
    """Import and run the LCD service after Django is ready."""

    from .runner import main as runner_main

    runner_main()


def main() -> None:
    """Bootstrap Django and start the LCD service."""

    _bootstrap_django()
    _run_lcd_service()


if __name__ == "__main__":  # pragma: no cover - script entry point
    main()
