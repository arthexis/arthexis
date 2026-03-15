import base64

from django.db.utils import DatabaseError

from .models import get_or_create_short_url
from .qr_utils import build_qr_png_bytes


def _encode_share_qr_data_uri(url: str) -> str:
    """Return a base64 QR image for the given share URL."""
    if not url:
        return ""
    png_bytes = build_qr_png_bytes(
        url,
        box_size=6,
        border=2,
        fill_color="#0b1420",
        back_color="white",
    )
    encoded = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def share_short_url(request):
    """Return short URL and QR metadata used by the public share modal."""
    if request is None:
        return {"share_short_url": "", "share_short_url_qr": ""}
    target_url = request.build_absolute_uri(request.path)
    share_url = target_url
    try:
        short_url = get_or_create_short_url(target_url)
    except DatabaseError:
        short_url = None
    if short_url:
        share_url = request.build_absolute_uri(short_url.redirect_path())

    try:
        qr_data_uri = _encode_share_qr_data_uri(share_url)
    except RuntimeError:
        qr_data_uri = ""

    return {"share_short_url": share_url, "share_short_url_qr": qr_data_uri}
