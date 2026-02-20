import logging
import ipaddress
import re
import socket

from django.contrib.sites.models import Site
from django.core.exceptions import DisallowedHost
from django.db.utils import OperationalError, ProgrammingError
from django.http import HttpRequest
from django.conf import settings

DEFAULT_BADGE_COLOR = "#28a745"
UNKNOWN_BADGE_COLOR = "#6c757d"
CAMERA_BADGE_COLOR = DEFAULT_BADGE_COLOR


logger = logging.getLogger(__name__)


def _resolve_request_host(request: HttpRequest) -> str:
    """Return the best-effort hostname for template badge lookups.

    ``request.get_host()`` can raise ``DisallowedHost`` when deployments are
    reached through an unexpected hostname. Admin templates should still render
    instead of failing with HTTP 500, so this helper falls back to raw WSGI
    metadata and strips any optional port suffix.
    """
    try:
        host_value = request.get_host()
    except DisallowedHost:
        host_value = request.META.get("HTTP_HOST") or request.META.get("SERVER_NAME", "")

    if host_value.startswith("["):
        host_value = host_value.split("]", 1)[0].lstrip("[")
    elif host_value.count(":") > 1:
        host_or_port = host_value.rsplit(":", 1)
        # Heuristic for non-standard bare IPv6-with-port input (e.g. ``::1:8080``):
        # strip a trailing numeric segment only when the remaining portion also
        # parses as IPv6. This reduces (but does not eliminate) false positives;
        # inputs like ``fc00::1:2:3:4`` may still be transformed because
        # ``fc00::1:2:3`` is itself valid IPv6. Proper Host syntax uses
        # brackets (``[::1]:8080``), so this path is best-effort fallback only.
        if (
            len(host_or_port) == 2
            and host_or_port[1].isdigit()
            and not host_or_port[0].endswith(":")
        ):
            try:
                ipaddress.ip_address(host_or_port[0])
            except ValueError:
                pass
            else:
                host_value = host_or_port[0]
    else:
        host_value = host_value.split(":", 1)[0]

    if not re.fullmatch(r"[A-Za-z0-9._-]+|[0-9A-Fa-f:.]+", host_value or ""):
        return ""
    return host_value


def site_and_node(request: HttpRequest):
    """Provide current Site, Node, and Role based on request host.

    Returns a dict with keys ``badge_site``, ``badge_node``, and ``badge_role``.
    ``badge_site`` is a ``Site`` instance or ``None`` if no match.
    ``badge_node`` is a ``Node`` instance or ``None`` if no match.
    ``badge_role`` is a ``NodeRole`` instance or ``None`` if the node is
    missing or unassigned.

    ``badge_site_color`` / ``badge_node_color`` / ``badge_role_color`` report
    the palette color used for the corresponding badge. Badges always use green
    when the entity is known and grey when the value cannot be determined.
    """
    host = _resolve_request_host(request)

    site = getattr(request, "badge_site", None) or getattr(request, "site", None)
    if site is None:
        try:
            site = Site.objects.filter(domain__iexact=host).first()
        except (OperationalError, ProgrammingError):
            site = None
    request.badge_site = site

    node = getattr(request, "badge_node", None) or getattr(request, "node", None)
    if node is None:
        try:
            from apps.nodes.models import Node

            node = Node.get_local()
            if not node:
                hostname = socket.gethostname()
                try:
                    addresses = socket.gethostbyname_ex(hostname)[2]
                except socket.gaierror:
                    addresses = []

                node = Node.objects.filter(hostname__iexact=hostname).first()
                if not node:
                    for addr in addresses:
                        node = Node.objects.filter(address=addr).first()
                        if node:
                            break
                if not node:
                    node = (
                        Node.objects.filter(hostname__iexact=host).first()
                        or Node.objects.filter(address=host).first()
                    )
        except Exception:
            logger.exception("Unexpected error resolving node for host '%s'", host)
            node = None
    request.badge_node = node

    role = getattr(request, "badge_role", None) or getattr(node, "role", None)
    request.badge_role = role

    video_device = getattr(request, "badge_video_device", None)
    if video_device is None and node is not None:
        try:
            from apps.video.models import VideoDevice

            video_device = (
                VideoDevice.objects.filter(node=node, is_default=True)
                .order_by("identifier")
                .first()
            )
        except (OperationalError, ProgrammingError):
            video_device = None
        except Exception:
            logger.exception(
                "Unexpected error resolving default video device for node %s", node
            )
            video_device = None
    request.badge_video_device = video_device

    role = getattr(node, "role", None)

    site_color = DEFAULT_BADGE_COLOR if site else UNKNOWN_BADGE_COLOR
    node_color = DEFAULT_BADGE_COLOR if node else UNKNOWN_BADGE_COLOR
    role_color = DEFAULT_BADGE_COLOR if role else UNKNOWN_BADGE_COLOR
    video_device_color = CAMERA_BADGE_COLOR if video_device else UNKNOWN_BADGE_COLOR

    site_name = site.name if site else ""
    node_role_name = role.name if role else ""
    return {
        "badge_site": site,
        "badge_node": node,
        "badge_role": role,
        "badge_video_device": video_device,
        # Public views fall back to the node role when the site name is blank.
        "badge_site_name": site_name or node_role_name,
        # Admin site badge uses the site display name if set, otherwise the domain.
        "badge_admin_site_name": site_name or (site.domain if site else ""),
        "badge_site_color": site_color,
        "badge_node_color": node_color,
        "badge_role_color": role_color,
        "badge_video_device_color": video_device_color,
        "current_site_domain": site.domain if site else host,
        "TIME_ZONE": settings.TIME_ZONE,
    }
