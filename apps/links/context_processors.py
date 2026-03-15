import base64

from django.db.utils import DatabaseError

from .models import get_or_create_short_url
from .qr_utils import build_qr_png_bytes


def _encode_share_qr_data_uri(url: str) -> str:
    """Encode a share URL as a QR image data URI.

    Parameters
    ----------
    url : str
        Absolute URL to encode as a QR code.

    Returns
    -------
    str
        PNG QR image encoded as a ``data:image/png;base64,...`` URI.

    Raises
    ------
    RuntimeError
        If QR code dependencies are unavailable.
    ValueError
        If QR color inputs are invalid.
    OSError
        If the image cannot be written to memory.
    """
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
    """Build public share-link context for the site share modal.

    Parameters
    ----------
    request : django.http.HttpRequest | None
        Current request used to build absolute share links.

    Returns
    -------
    dict[str, str]
        Mapping containing ``share_short_url`` and ``share_short_url_qr``.

    Raises
    ------
    None
        Exceptions from QR generation are handled and converted to an empty QR value.
    """
    if request is None:
        return {"share_short_url": "", "share_short_url_qr": ""}
    share_url = request.build_absolute_uri(request.path)
    try:
        short_url = get_or_create_short_url(share_url)
    except DatabaseError:
        short_url = None
    if short_url:
        share_url = request.build_absolute_uri(short_url.redirect_path())

    try:
        qr_data_uri = _encode_share_qr_data_uri(share_url)
    except Exception:
        qr_data_uri = ""

    return {"share_short_url": share_url, "share_short_url_qr": qr_data_uri}
