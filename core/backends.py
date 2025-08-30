"""Custom authentication backends for the core app."""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
import ipaddress

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


class LocalhostAdminBackend(ModelBackend):
    """Allow default admin credentials only from local networks."""

    _ALLOWED_NETWORKS = [
        ipaddress.ip_network("::1/128"),
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("10.42.0.0/16"),
    ]

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
            allowed = any(ip in net for net in self._ALLOWED_NETWORKS)
            if not allowed:
                return None
        return super().authenticate(request, username, password, **kwargs)

