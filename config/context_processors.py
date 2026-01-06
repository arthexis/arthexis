import socket

from django.contrib.sites.models import Site
from django.db.utils import OperationalError, ProgrammingError
from django.http import HttpRequest
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

DEFAULT_BADGE_COLOR = "#28a745"
UNKNOWN_BADGE_COLOR = "#6c757d"


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
            node = None
    request.badge_node = node

    role = getattr(request, "badge_role", None) or getattr(node, "role", None)
    request.badge_role = role

    role = getattr(node, "role", None)

    site_color = DEFAULT_BADGE_COLOR if site else UNKNOWN_BADGE_COLOR
    node_color = DEFAULT_BADGE_COLOR if node else UNKNOWN_BADGE_COLOR
    role_color = DEFAULT_BADGE_COLOR if role else UNKNOWN_BADGE_COLOR

    site_name = site.name if site else ""
    node_role_name = role.name if role else ""

    clock_time = timezone.now()
    clock_timezone = timezone.get_current_timezone_name()
    clock_url = reverse("admin:clocks_clockdevice_changelist")
    try:
        from apps.clocks.models import ClockDevice
        from apps.clocks.utils import read_hardware_clock_time
    except Exception:
        ClockDevice = None  # type: ignore
        read_hardware_clock_time = None  # type: ignore

    if ClockDevice is not None:
        try:
            clock_device = None
            if node:
                clock_device = (
                    ClockDevice.objects.filter(node=node)
                    .order_by("bus", "address", "pk")
                    .first()
                )
            if clock_device is None:
                clock_device = ClockDevice.objects.order_by("bus", "address", "pk").first()

            if clock_device:
                if clock_device.enable_public_view and clock_device.public_view_slug:
                    clock_url = reverse(
                        "clockdevice-public-view", args=[clock_device.public_view_slug]
                    )
                else:
                    clock_url = reverse(
                        "admin:clocks_clockdevice_change", args=[clock_device.pk]
                    )

                if read_hardware_clock_time:
                    clock_value = read_hardware_clock_time()
                    if clock_value:
                        local_time = clock_value.astimezone(
                            timezone.get_current_timezone()
                        )
                        clock_time = local_time
                        clock_timezone = (
                            clock_value.tzname()
                            or timezone.get_current_timezone_name()
                        )
        except (OperationalError, ProgrammingError):
            pass

    return {
        "badge_site": site,
        "badge_node": node,
        "badge_role": role,
        # Public views fall back to the node role when the site name is blank.
        "badge_site_name": site_name or node_role_name,
        # Admin site badge uses the site display name if set, otherwise the domain.
        "badge_admin_site_name": site_name or (site.domain if site else ""),
        "badge_site_color": site_color,
        "badge_node_color": node_color,
        "badge_role_color": role_color,
        "current_site_domain": site.domain if site else host,
        "TIME_ZONE": settings.TIME_ZONE,
        "admin_clock_time": clock_time,
        "admin_clock_timezone": clock_timezone,
        "admin_clock_url": clock_url,
    }
