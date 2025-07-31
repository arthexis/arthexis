from django.contrib.auth import get_user_model
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
