from django.contrib.auth import get_user_model


class RFIDBackend:
    """Authenticate using a user's RFID UID."""

    def authenticate(self, request, rfid_uid=None, **kwargs):
        if not rfid_uid:
            return None
        User = get_user_model()
        try:
            return User.objects.get(rfid_uid=rfid_uid)
        except User.DoesNotExist:
            return None

    def get_user(self, user_id):
        User = get_user_model()
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
