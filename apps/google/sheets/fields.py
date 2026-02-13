"""Custom model fields for Google Sheets integration."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models


def _encryption_key() -> bytes:
    """Return a stable Fernet key derived from ``SECRET_KEY``."""

    secret = settings.SECRET_KEY.encode("utf-8")
    digest = hashlib.sha256(secret).digest()
    return base64.urlsafe_b64encode(digest)


def _encrypt(value: str) -> str:
    """Encrypt a string value for storage."""

    return Fernet(_encryption_key()).encrypt(value.encode("utf-8")).decode("utf-8")


def _decrypt(value: str) -> str | None:
    """Decrypt a string value, returning ``None`` if it is not decryptable."""

    try:
        return Fernet(_encryption_key()).decrypt(value.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError, UnicodeDecodeError):
        return None


class EncryptedTextField(models.TextField):
    """A TextField that encrypts values at rest using Fernet."""

    description = "Encrypted text"

    def from_db_value(self, value, expression, connection):
        if value in (None, ""):
            return value
        decrypted = _decrypt(value)
        return decrypted if decrypted is not None else value

    def to_python(self, value):
        if value in (None, "") or not isinstance(value, str):
            return value
        decrypted = _decrypt(value)
        return decrypted if decrypted is not None else value

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value in (None, ""):
            return value
        if not isinstance(value, str):
            value = str(value)
        if _decrypt(value) is not None:
            return value
        return _encrypt(value)
