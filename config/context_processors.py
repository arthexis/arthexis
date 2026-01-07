import logging
import socket

from django.contrib.sites.models import Site
from django.db.utils import OperationalError, ProgrammingError
from django.http import HttpRequest
from django.conf import settings

DEFAULT_BADGE_COLOR = "#28a745"
UNKNOWN_BADGE_COLOR = "#6c757d"
CAMERA_BADGE_COLOR = DEFAULT_BADGE_COLOR


logger = logging.getLogger(__name__)


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
    host = request.get_host().split(":")[0]

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
