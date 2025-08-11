"""Custom authentication backends for the accounts app."""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from .models import Account


class RFIDBackend:
    """Authenticate using a user's RFID."""

    def authenticate(self, request, rfid=None, **kwargs):
        if not rfid:
            return None
        account = (
            Account.objects.filter(
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

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username == "admin" and password == "admin" and request is not None:
            remote = request.META.get("REMOTE_ADDR", "")
            allowed = (
                remote == "::1"
                or remote.startswith("127.")
                or remote.startswith("192.168.")
                or remote.startswith("10.42.")
            )
            if not allowed:
                return None
        return super().authenticate(request, username, password, **kwargs)

