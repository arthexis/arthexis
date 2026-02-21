"""Models for Evergo user credential and profile synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import unquote, urlsplit
from typing import Any

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from encrypted_model_fields.fields import EncryptedCharField, EncryptedTextField
from apps.users.models import Profile

from .exceptions import EvergoAPIError


@dataclass(slots=True)
class EvergoLoginResult:
    """Normalized outcome returned by a successful Evergo login request."""

    payload: dict[str, Any]
    response_code: int


class EvergoUser(Profile):
    """Stores Evergo credentials and synchronized remote profile data for a Suite owner."""

    API_LOGIN_URL = getattr(
        settings,
        "EVERGO_API_LOGIN_URL",
        "https://portal-backend.evergo.com/api/mex/v1/login",
    )
    PORTAL_LOGIN_URL = getattr(
        settings,
        "EVERGO_PORTAL_LOGIN_URL",
        "https://portal-mex.evergo.com/access/login",
    )

    profile_fields = ("evergo_email", "evergo_password")

    evergo_email = models.EmailField(blank=True)
    evergo_password = EncryptedCharField(max_length=255, blank=True)

    evergo_user_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)

    empresa_id = models.PositiveIntegerField(null=True, blank=True)
    empresa_name = models.CharField(max_length=255, blank=True)
    subempresa_id = models.PositiveIntegerField(null=True, blank=True)
    subempresa_name = models.CharField(max_length=255, blank=True)

    two_fa_enabled = models.BooleanField(default=False)
    two_fa_authenticated = models.BooleanField(default=False)
    two_factor_secret = EncryptedTextField(blank=True)
    two_factor_recovery_codes = EncryptedTextField(blank=True)
    two_factor_confirmed_at = models.DateTimeField(null=True, blank=True)

    evergo_created_at = models.DateTimeField(null=True, blank=True)
    evergo_updated_at = models.DateTimeField(null=True, blank=True)
    last_login_test_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Evergo User"
        verbose_name_plural = "Evergo Users"

    def __str__(self) -> str:
        """Return a readable identifier for admin lists."""
        return self.name or self.email or self.evergo_email or f"EvergoUser#{self.pk}"

    def clean(self) -> None:
        """Validate credentials when one value is provided without the other."""
        super().clean()
        if bool(self.evergo_email) ^ bool(self.evergo_password):
            raise ValidationError(
                "Both evergo_email and evergo_password are required to test login."
            )

    def test_login(self, *, timeout: int = 15) -> EvergoLoginResult:
        """Authenticate against Evergo and update profile metadata from the response."""
        if not self.evergo_email or not self.evergo_password:
            raise EvergoAPIError("Evergo credentials are incomplete.")

        try:
            with requests.Session() as session:
                xsrf_token = self._prime_session(session=session, timeout=timeout)
                response = session.post(
                    self.API_LOGIN_URL,
                    json={"email": self.evergo_email, "password": self.evergo_password},
                    headers=self._build_login_headers(xsrf_token=xsrf_token),
                    timeout=timeout,
                )
        except requests.RequestException as exc:
            raise EvergoAPIError(f"Unable to connect to Evergo API: {exc}") from exc

        if response.status_code >= 400:
            if response.status_code == 419:
                raise EvergoAPIError(
                    "Evergo login failed with status 419 (CSRF/session validation failed)."
                )
            raise EvergoAPIError(
                f"Evergo login failed with status {response.status_code}."
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise EvergoAPIError("Evergo login returned non-JSON payload.") from exc

        if not isinstance(payload, dict):
            raise EvergoAPIError("Evergo login payload must be a JSON object.")

        self.apply_login_payload(payload)
        self.last_login_test_at = timezone.now()
        self.save()
        return EvergoLoginResult(payload=payload, response_code=response.status_code)

    def _prime_session(self, *, session: requests.Session, timeout: int) -> str:
        """Load Evergo portal login page to establish cookies and recover the CSRF token."""
        response = session.get(
            self.PORTAL_LOGIN_URL,
            headers={"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
            timeout=timeout,
        )
        response.raise_for_status()
        xsrf_token = session.cookies.get("XSRF-TOKEN")
        if not xsrf_token:
            raise EvergoAPIError("Evergo login failed: missing XSRF-TOKEN cookie.")
        return unquote(xsrf_token)

    def _build_login_headers(self, *, xsrf_token: str) -> dict[str, str]:
        """Build headers expected by Evergo backend for authenticated login requests."""
        parsed_portal_url = urlsplit(self.PORTAL_LOGIN_URL)
        origin = f"{parsed_portal_url.scheme}://{parsed_portal_url.netloc}"
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "origin": origin,
            "referer": self.PORTAL_LOGIN_URL,
            "x-xsrf-token": xsrf_token,
        }

    def apply_login_payload(self, payload: dict[str, Any]) -> None:
        """Map Evergo API user payload into local tracking fields."""
        self.evergo_user_id = _to_int(payload.get("id"))
        self.name = str(payload.get("name") or "")
        self.email = str(payload.get("email") or "")
        self.two_fa_enabled = bool(_to_int(payload.get("two_fa_enabled")))
        self.two_fa_authenticated = bool(_to_int(payload.get("two_fa_authenticated")))
        self.two_factor_secret = str(payload.get("two_factor_secret") or "")
        self.two_factor_recovery_codes = str(
            payload.get("two_factor_recovery_codes") or ""
        )
        self.two_factor_confirmed_at = _parse_dt(payload.get("two_factor_confirmed_at"))

        subempresa = _first_dict(payload.get("subempresas"))
        self.subempresa_id = _to_int(subempresa.get("id"))
        self.subempresa_name = str(subempresa.get("nombre") or "")
        self.empresa_id = _to_int(subempresa.get("idInstalaEmpresa"))
        self.empresa_name = str(subempresa.get("empresa") or "")

        self.evergo_created_at = _parse_dt(payload.get("created_at"))
        self.evergo_updated_at = _parse_dt(payload.get("updated_at"))


def _to_int(value: Any) -> int | None:
    """Convert loosely typed API integers into local integer fields."""
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_dict(value: Any) -> dict[str, Any]:
    """Return the first dictionary in a list-like payload field."""
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return {}


def _parse_dt(value: Any) -> datetime | None:
    """Parse ISO datetimes produced by the Evergo API."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    dt = parse_datetime(value)
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt
