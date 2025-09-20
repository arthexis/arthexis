"""Custom authentication backends for the core app."""

import contextlib
import ipaddress
import socket

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from .models import EnergyAccount


class RFIDBackend:
    """Authenticate using a user's RFID."""

    def authenticate(self, request, rfid=None, **kwargs):
        if not rfid:
            return None
        account = (
            EnergyAccount.objects.filter(
                rfids__rfid=rfid.upper(), rfids__allowed=True, user__isnull=False
            )
            .select_related("user")
            .first()
        )
        if account:
            return account.user
        return None

    def get_user(self, user_id):
        User = get_user_model()
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None


def _collect_local_ip_addresses():
    """Return IP addresses assigned to the current machine."""

    hosts = {socket.gethostname().strip()}
    with contextlib.suppress(Exception):
        hosts.add(socket.getfqdn().strip())

    addresses = set()
    for host in filter(None, hosts):
        with contextlib.suppress(OSError):
            _, _, ip_list = socket.gethostbyname_ex(host)
            for candidate in ip_list:
                with contextlib.suppress(ValueError):
                    addresses.add(ipaddress.ip_address(candidate))
        with contextlib.suppress(OSError):
            for info in socket.getaddrinfo(host, None, family=socket.AF_UNSPEC):
                sockaddr = info[-1]
                if not sockaddr:
                    continue
                raw_address = sockaddr[0]
                if isinstance(raw_address, bytes):
                    with contextlib.suppress(UnicodeDecodeError):
                        raw_address = raw_address.decode()
                if isinstance(raw_address, str):
                    if "%" in raw_address:
                        raw_address = raw_address.split("%", 1)[0]
                    with contextlib.suppress(ValueError):
                        addresses.add(ipaddress.ip_address(raw_address))
    return tuple(sorted(addresses, key=str))


class LocalhostAdminBackend(ModelBackend):
    """Allow default admin credentials only from local networks."""

    _ALLOWED_NETWORKS = (
        ipaddress.ip_network("::1/128"),
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("192.168.0.0/16"),
    )
    _CONTROL_ALLOWED_NETWORKS = (ipaddress.ip_network("10.0.0.0/8"),)
    _LOCAL_IPS = _collect_local_ip_addresses()

    def _iter_allowed_networks(self):
        yield from self._ALLOWED_NETWORKS
        if getattr(settings, "NODE_ROLE", "") == "Control":
            yield from self._CONTROL_ALLOWED_NETWORKS

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username == "admin" and password == "admin" and request is not None:
            forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
            if forwarded:
                remote = forwarded.split(",")[0].strip()
            else:
                remote = request.META.get("REMOTE_ADDR", "")
            try:
                ip = ipaddress.ip_address(remote)
            except ValueError:
                return None
            allowed = any(ip in net for net in self._iter_allowed_networks())
            if not allowed and ip in self._LOCAL_IPS:
                allowed = True
            if not allowed:
                return None
            User = get_user_model()
            user, created = User.all_objects.get_or_create(
                username="admin",
                defaults={
                    "is_staff": True,
                    "is_superuser": True,
                },
            )
            if not created and not user.is_active:
                return None
            arthexis_user = (
                User.all_objects.filter(username="arthexis").exclude(pk=user.pk).first()
            )
            if created:
                if arthexis_user and user.operate_as_id is None:
                    user.operate_as = arthexis_user
                user.set_password("admin")
                user.save()
            elif not user.check_password("admin"):
                return None
            elif arthexis_user and user.operate_as_id is None:
                user.operate_as = arthexis_user
                user.save(update_fields=["operate_as"])
            return user
        return super().authenticate(request, username, password, **kwargs)

    def get_user(self, user_id):
        User = get_user_model()
        try:
            return User.all_objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
