"""Standalone helpers for interacting with the MFRC522 RFID reader."""

from __future__ import annotations

import select
import sys
import time
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence, TextIO

PINOUT: "OrderedDict[str, str]" = OrderedDict(
    [
        ("SDA", "GPIO 8 / CE0"),
        ("SCK", "GPIO 11 / SCLK"),
        ("MOSI", "GPIO 10 / MOSI"),
        ("MISO", "GPIO 9 / MISO"),
        ("IRQ", "GPIO 4"),
        ("GND", "Ground"),
        ("RST", "GPIO 25"),
        ("3v3", "3.3V"),
    ]
)

DEFAULT_SPI_DEVICE = Path("/dev/spidev0.0")


def pinout() -> "OrderedDict[str, str]":
    """Return a copy of the expected MFRC522 wiring map."""

    return PINOUT.copy()


def _print(message: str, stream: TextIO) -> None:
    stream.write(f"{message}\n")
    stream.flush()


def _load_dependencies(stdout: TextIO):
    """Import hardware dependencies and return ``(SimpleMFRC522, GPIO)``."""

    try:  # pragma: no cover - exercised via tests with monkeypatching
        from mfrc522 import SimpleMFRC522  # type: ignore
    except ModuleNotFoundError as exc:
        missing = exc.name or "mfrc522"
        if missing == "mfrc522":
            _print(
                "The 'mfrc522' package is required. Install it with 'pip install mfrc522'.",
                stdout,
            )
        elif missing == "spidev":
            _print(
                "The 'spidev' package is required to access the SPI bus. Install it with 'pip install spidev'.",
                stdout,
            )
        else:
            _print(f"Required dependency '{missing}' is missing.", stdout)
        return None, None
    except Exception as exc:  # pragma: no cover - defensive fallback
        _print(f"Failed to import mfrc522.SimpleMFRC522: {exc}", stdout)
        return None, None

    try:  # pragma: no cover - exercised via tests with monkeypatching
        import RPi.GPIO as GPIO  # type: ignore
    except ModuleNotFoundError:
        _print(
            "The 'RPi.GPIO' package is required. Install it with 'pip install RPi.GPIO'.",
            stdout,
        )
        return None, None
    except Exception as exc:  # pragma: no cover - defensive fallback
        _print(f"Failed to import RPi.GPIO: {exc}", stdout)
        return None, None

    return SimpleMFRC522, GPIO


def _iter_spi_devices() -> Iterable[Path]:  # pragma: no cover - filesystem probe
    return sorted(Path("/dev").glob("spidev*"))


def _report_missing_spi(device: Path, stdout: TextIO) -> None:
    _print(f"SPI device '{device}' was not found.", stdout)
    candidates = list(_iter_spi_devices())
    if candidates:
        _print("Detected alternate SPI devices:", stdout)
        for candidate in candidates:
            _print(f"  - {candidate}", stdout)
        _print(
            "Move the reader to the matching bus/device or update the scanner configuration.",
            stdout,
        )
    else:
        _print(
            "No SPI devices are available. Enable SPI via 'raspi-config', ensure 'dtparam=spi=on' in /boot/config.txt,",
            stdout,
        )
        _print(
            "load the 'spi_bcm2835' and 'spidev' kernel modules, and reboot before retrying.",
            stdout,
        )


def scan(
    *,
    spi_device: Path | str = DEFAULT_SPI_DEVICE,
    poll_interval: float = 0.1,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
    select_fn: Callable[
        [Sequence[TextIO], Sequence[TextIO], Sequence[TextIO], float], tuple
    ] = select.select,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Interactively read RFID tags until the user presses Enter."""

    stdout = stdout or sys.stdout
    stdin = stdin or sys.stdin

    reader_factory, gpio_module = _load_dependencies(stdout)
    if reader_factory is None or gpio_module is None:
        return 1

    device = Path(spi_device)
    if not device.exists():
        _report_missing_spi(device, stdout)
        return 1

    try:
        reader = reader_factory()
    except FileNotFoundError as exc:
        _print(
            "Unable to access the SPI device. Ensure SPI is enabled and the reader is wired to the correct pins.",
            stdout,
        )
        _print(str(exc), stdout)
        return 1
    except PermissionError as exc:
        _print(
            "Permission denied while accessing the SPI device. Run as root or add the user to the 'spi' group.",
            stdout,
        )
        _print(str(exc), stdout)
        return 1
    except Exception as exc:  # pragma: no cover - defensive fallback
        _print(f"Failed to initialize the RFID reader: {exc}", stdout)
        return 1

    _print("Scanning for RFID cards. Press Enter to stop.", stdout)
    try:
        while True:
            try:
                ready, _, _ = select_fn([stdin], [], [], 0)
            except Exception:  # pragma: no cover - defensive fallback
                ready = []
            if ready:
                try:
                    stdin.readline()
                except Exception:  # pragma: no cover - best effort cleanup
                    pass
                break

            try:
                card_id, text = reader.read_no_block()
            except Exception as exc:  # pragma: no cover - hardware failure
                _print(f"RFID read failed: {exc}", stdout)
                break

            if card_id:
                clean_text = " ".join((text or "").split())
                if clean_text:
                    _print(f"Tag {card_id}: {clean_text}", stdout)
                else:
                    _print(f"Tag {card_id}", stdout)

            sleep(max(poll_interval, 0))
    except KeyboardInterrupt:  # pragma: no cover - interactive exit
        _print("Scan interrupted by user.", stdout)
    finally:
        try:
            gpio_module.cleanup()
        except Exception:  # pragma: no cover - best effort cleanup
            pass

    return 0


def main() -> int:
    """Module entry point for ``python -m projects.rfid``."""

    return scan()


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
