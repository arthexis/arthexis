"""QR label rendering and printer helpers for the links app."""

from __future__ import annotations

import ctypes
import os
import sys
import time
from collections.abc import Iterable
from ctypes import wintypes
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from apps.links.qr_utils import _load_qrcode_module

DEFAULT_LABEL_WIDTH = 320
DEFAULT_LABEL_HEIGHT = 240
DEFAULT_QR_SIZE = 160
DEFAULT_CHUNK_BYTES = 16
DEFAULT_CHUNK_DELAY_SECONDS = 0.04
PHOMEMO_M220_USBPRINT_INTERFACE_GUID = "{28d78fad-5a12-11d1-ae5b-0000f803a8c2}"
PHOMEMO_M220_USB_VID_PID = "VID_0483&PID_5740"
PHOMEMO_M220_USB_PATH_ENV = "ARTHEXIS_QR_PRINTER_USB_PATH"


@dataclass(frozen=True)
class QRLabelSpec:
    """Layout options for a compact QR sticker label."""

    width: int = DEFAULT_LABEL_WIDTH
    height: int = DEFAULT_LABEL_HEIGHT
    qr_size: int = DEFAULT_QR_SIZE
    title: str = ""
    subtitle: str = ""
    footer: str = ""
    margin: int = 4


def wifi_escape(value: str) -> str:
    """Escape Wi-Fi QR payload fields."""

    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace(":", "\\:")
        .replace('"', '\\"')
    )


def build_wifi_payload(
    ssid: str,
    password: str = "",
    *,
    auth_type: str = "WPA",
    hidden: bool = False,
) -> str:
    """Return a standards-compatible Wi-Fi QR payload."""

    cleaned_ssid = ssid.strip()
    if not cleaned_ssid:
        raise ValueError("Wi-Fi SSID is required")
    cleaned_auth_type = (auth_type or "WPA").strip() or "WPA"
    fields = [
        f"T:{wifi_escape(cleaned_auth_type)}",
        f"S:{wifi_escape(cleaned_ssid)}",
    ]
    if cleaned_auth_type.lower() != "nopass":
        fields.append(f"P:{wifi_escape(password)}")
    fields.append(f"H:{str(bool(hidden)).lower()}")
    return "WIFI:" + ";".join(fields) + ";;"


def render_qr_png_bytes(payload: str, spec: QRLabelSpec | None = None) -> bytes:
    """Return a PNG preview for a QR label."""

    image = build_qr_label_image(payload, spec=spec)
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def build_qr_label_image(payload: str, spec: QRLabelSpec | None = None) -> Image.Image:
    """Render a QR code on a compact monochrome label canvas."""

    if not payload:
        raise ValueError("QR payload is required")
    spec = spec or QRLabelSpec()
    if spec.width <= 0 or spec.height <= 0:
        raise ValueError("Label dimensions must be positive")
    if spec.width % 8:
        raise ValueError("Label width must be divisible by 8 for raster printing")

    canvas = Image.new("L", (spec.width, spec.height), 255)
    qr_image = _make_qr_image(payload)
    qr_size = max(24, min(spec.qr_size, spec.width - (spec.margin * 2), spec.height - 56))
    qr_image = qr_image.resize((qr_size, qr_size), Image.Resampling.NEAREST)
    canvas.paste(qr_image, ((spec.width - qr_size) // 2, spec.margin))

    draw = ImageDraw.Draw(canvas)
    title_font = _load_font(22, bold=True)
    text_font = _load_font(16, bold=False)
    small_font = _load_font(14, bold=False)
    text_y = spec.margin + qr_size + 4
    max_text_width = spec.width - (spec.margin * 2)

    for text, font, gap in (
        (spec.title, title_font, 4),
        (spec.subtitle, text_font, 2),
        (spec.footer, small_font, 0),
    ):
        if not text:
            continue
        display = _truncate_to_width(draw, text.strip(), font, max_text_width)
        if not display:
            continue
        bbox = draw.textbbox((0, 0), display, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        if text_y + text_height > spec.height:
            break
        draw.text(((spec.width - text_width) // 2, text_y), display, fill=0, font=font)
        text_y += text_height + gap

    return canvas


def _make_qr_image(payload: str) -> Image.Image:
    qrcode = _load_qrcode_module()
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=3,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("L")


def _load_font(size: int, *, bold: bool) -> ImageFont.ImageFont:
    candidates = (
        (
            "arialbd.ttf",
            "Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
        )
        if bold
        else (
            "arial.ttf",
            "Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
            "/Library/Fonts/Arial.ttf",
        )
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _truncate_to_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> str:
    if not text:
        return ""
    if _text_width(draw, text, font) <= max_width:
        return text
    suffix = "..."
    available = max_width - _text_width(draw, suffix, font)
    if available <= 0:
        return ""
    low = 0
    high = len(text)
    while low < high:
        midpoint = (low + high + 1) // 2
        candidate = text[:midpoint].rstrip()
        if _text_width(draw, candidate, font) <= available:
            low = midpoint
        else:
            high = midpoint - 1
    return text[:low].rstrip() + suffix if low else ""


def _text_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def pack_monochrome_raster(image: Image.Image, *, threshold: int = 180) -> tuple[int, int, bytes]:
    """Pack a label image into the one-bit row raster used by Phomemo M220."""

    width, height = image.size
    if width % 8:
        raise ValueError("Image width must be divisible by 8")
    bytes_per_line = width // 8
    black_white = image.convert("L").point(lambda pixel: 0 if pixel < threshold else 255, "1")
    pixels = black_white.load()
    raster = bytearray()
    for y in range(height):
        for x_byte in range(bytes_per_line):
            value = 0
            for bit in range(8):
                if pixels[x_byte * 8 + bit, y] == 0:
                    value |= 0x80 >> bit
            raster.append(value)
    return bytes_per_line, height, bytes(raster)


def build_phomemo_m220_job(
    image: Image.Image,
    *,
    speed: int = 2,
    density: int = 15,
    media_type: int = 0x0A,
) -> bytes:
    """Build a raw Phomemo M220 raster print job."""

    for name, value in (("speed", speed), ("density", density), ("media_type", media_type)):
        if value < 0 or value > 255:
            raise ValueError(f"{name} must fit in one byte")
    bytes_per_line, height, raster = pack_monochrome_raster(image)
    command = bytearray()
    command += b"\x1b\x4e\x0d" + bytes([speed])
    command += b"\x1b\x4e\x04" + bytes([density])
    command += b"\x1f\x11" + bytes([media_type])
    command += b"\x1d\x76\x30\x00"
    command += bytes(
        [
            bytes_per_line & 0xFF,
            (bytes_per_line >> 8) & 0xFF,
            height & 0xFF,
            (height >> 8) & 0xFF,
        ]
    )
    command += raster
    command += b"\x1f\xf0\x05\x00"
    command += b"\x1f\xf0\x03\x00"
    return bytes(command)


def iter_phomemo_m220_usb_paths() -> Iterable[str]:
    """Yield Windows USBPRINT interface paths that look like a Phomemo M220."""

    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:  # pragma: no cover - winreg is Windows-only
        return []

    key_path = (
        "SYSTEM\\CurrentControlSet\\Control\\DeviceClasses\\"
        f"{PHOMEMO_M220_USBPRINT_INTERFACE_GUID}"
    )
    try:
        root_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path)
    except OSError:
        return []

    paths: list[str] = []
    with root_key:
        index = 0
        while True:
            try:
                subkey_name = winreg.EnumKey(root_key, index)
            except OSError:
                break
            index += 1
            normalized = subkey_name.upper()
            if PHOMEMO_M220_USB_VID_PID not in normalized:
                continue
            if not subkey_name.startswith("##?#"):
                continue
            paths.append("\\\\?\\" + subkey_name[4:])
    return paths


def resolve_phomemo_m220_usb_path(explicit_path: str | None = None) -> str:
    """Return an explicit, environment, or auto-discovered M220 USB path."""

    if explicit_path and explicit_path.strip():
        return explicit_path.strip()
    env_path = os.environ.get(PHOMEMO_M220_USB_PATH_ENV, "").strip()
    if env_path:
        return env_path
    candidates = list(iter_phomemo_m220_usb_paths())
    return candidates[0] if len(candidates) == 1 else ""


def write_windows_usb(
    usb_path: str,
    data: bytes,
    *,
    chunk_size: int = DEFAULT_CHUNK_BYTES,
    delay_seconds: float = DEFAULT_CHUNK_DELAY_SECONDS,
) -> int:
    """Write bytes to a Windows USB device path."""

    if sys.platform != "win32":
        raise OSError("Windows USB device writes are only supported on Windows")
    if not usb_path:
        raise ValueError("USB path is required")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.WriteFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel32.WriteFile.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateFileW(
        usb_path,
        0x40000000,
        0x00000001 | 0x00000002,
        None,
        3,
        0,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), f"CreateFileW failed for {usb_path}")

    total = 0
    try:
        for offset in range(0, len(data), chunk_size):
            chunk = data[offset : offset + chunk_size]
            written = wintypes.DWORD(0)
            buffer = ctypes.create_string_buffer(chunk)
            ok = kernel32.WriteFile(
                handle,
                buffer,
                len(chunk),
                ctypes.byref(written),
                None,
            )
            if not ok:
                raise OSError(ctypes.get_last_error(), f"WriteFile failed at offset {offset}")
            total += written.value
            if written.value != len(chunk):
                raise OSError(
                    ctypes.get_last_error(),
                    (
                        f"short write at offset {offset}: "
                        f"{written.value}/{len(chunk)}"
                    ),
                )
            if delay_seconds:
                time.sleep(delay_seconds)
    finally:
        kernel32.CloseHandle(handle)
    return total
