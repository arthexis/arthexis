from __future__ import annotations

import functools
from io import BytesIO


@functools.lru_cache(maxsize=1)
def _load_qrcode_module():
    """Return the optional ``qrcode`` module when available."""
    try:
        import qrcode
    except ModuleNotFoundError as exc:
        if exc.name == "qrcode":
            raise RuntimeError(
                "The 'qrcode' package is required to generate QR images. "
                "Install project optional dependency group 'nodes' or add qrcode."
            ) from exc
        raise
    return qrcode


def build_qr_png_bytes(
    url: str,
    *,
    box_size: int = 6,
    border: int = 2,
    fill_color: str = "black",
    back_color: str = "white",
) -> bytes:
    """Return a PNG QR image for ``url`` as raw bytes."""
    qrcode = _load_qrcode_module()
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
    """Return an SVG QR image for ``url`` as UTF-8 text."""
    qrcode = _load_qrcode_module()
    from qrcode.image.svg import SvgImage

    qr = qrcode.QRCode(box_size=box_size, border=border)
    qr.add_data(url)
    qr.make(fit=True)
    svg_image = qr.make_image(image_factory=SvgImage)
    buffer = BytesIO()
    svg_image.save(buffer)
    return buffer.getvalue().decode("utf-8")
