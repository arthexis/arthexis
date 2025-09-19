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
            User = get_user_model()
            user, created = User.all_objects.get_or_create(
                username="admin",
                defaults={
                    "is_staff": True,
                    "is_superuser": True,
                },
            )
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
