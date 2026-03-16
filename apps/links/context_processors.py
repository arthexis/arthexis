import base64
from urllib.parse import urlsplit

from django.contrib.sites.models import Site
from django.core.exceptions import DisallowedHost
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

    def _build_absolute_with_fallback(path: str) -> str:
        """Build an absolute URI and fall back safely when host validation fails.

        Parameters
        ----------
        path : str
            Path that should be converted into an absolute URI.

        Returns
        -------
        str
            Absolute URI when host validation succeeds; otherwise the input path.

        Raises
        ------
        None
            ``DisallowedHost`` is handled internally to avoid using untrusted host headers.
        """
        try:
            return request.build_absolute_uri(path)
        except DisallowedHost:
            raw_host = (request.META.get("HTTP_HOST") or request.META.get("SERVER_NAME") or "").strip()
            if not raw_host:
                return path

            try:
                parsed_host = urlsplit(f"//{raw_host}")
            except ValueError:
                return path

            host_only = (parsed_host.hostname or "").strip().lower().rstrip(".")
            if not host_only:
                return path

            try:
                request_port = parsed_host.port
            except ValueError:
                return path

            try:
                site_domain = (Site.objects.get_current().domain or "").strip().lower().rstrip(".")
                parsed_site = urlsplit(f"//{site_domain}")
                site_host = (parsed_site.hostname or "").strip().lower().rstrip(".")
                site_port = parsed_site.port
            except (AttributeError, DatabaseError, Site.DoesNotExist):
                site_host = ""
                site_port = None
            except ValueError:
                site_host = ""
                site_port = None

            if site_host and host_only == site_host:
                if site_port is not None and request_port != site_port:
                    return path
                scheme = request.scheme or "http"
                safe_host = host_only or ""
                if ":" in safe_host and not safe_host.startswith("["):
                    safe_host = f"[{safe_host}]"
                if request_port is not None:
                    safe_host = f"{safe_host}:{request_port}"
                return f"{scheme}://{safe_host}{path}"

            return path

    share_url = _build_absolute_with_fallback(request.path)
    try:
        short_url = get_or_create_short_url(share_url)
    except DatabaseError:
        short_url = None
    if short_url:
        redirect_path = short_url.redirect_path()
        share_url = _build_absolute_with_fallback(redirect_path)

    try:
        qr_data_uri = _encode_share_qr_data_uri(share_url)
    except Exception:
        qr_data_uri = ""

    return {"share_short_url": share_url, "share_short_url_qr": qr_data_uri}
