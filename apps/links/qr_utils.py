from __future__ import annotations

from io import BytesIO

import qrcode
from qrcode.image.svg import SvgImage


def build_qr_png_bytes(
    url: str,
    *,
    box_size: int = 6,
    border: int = 2,
    fill_color: str = "black",
    back_color: str = "white",
) -> bytes:
    qr = qrcode.QRCode(box_size=box_size, border=border)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color=fill_color, back_color=back_color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def build_qr_svg_text(
    url: str,
    *,
    box_size: int = 6,
    border: int = 2,
) -> str:
    qr = qrcode.QRCode(box_size=box_size, border=border)
    qr.add_data(url)
    qr.make(fit=True)
    svg_image = qr.make_image(image_factory=SvgImage)
    buffer = BytesIO()
    svg_image.save(buffer)
    return buffer.getvalue().decode("utf-8")
