"""Models for Evergo credential, profile, and order synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
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
    API_SITIOS_URL = getattr(
        settings,
        "EVERGO_API_SITIOS_URL",
        "https://portal-backend.evergo.com/api/mex/v1/config/catalogs/sitios/all",
    )
    API_INGENIEROS_URL = getattr(
        settings,
        "EVERGO_API_INGENIEROS_URL",
        "https://portal-backend.evergo.com/api/mex/v1/ordenes/search-ingenieros",
    )
    API_ORDEN_ESTATUS_URL = getattr(
        settings,
        "EVERGO_API_ORDEN_ESTATUS_URL",
        "https://portal-backend.evergo.com/api/mex/v1/config/catalogs/orden-estatus",
    )
    API_ORDERS_URL = getattr(
        settings,
        "EVERGO_API_ORDERS_URL",
        "https://portal-backend.evergo.com/api/mex/v1/ordenes/instalador-coordinador",
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

    def load_orders(self, *, timeout: int = 20) -> tuple[int, int]:
        """Fetch and upsert Evergo orders assigned to this user into local models."""
        if not self.evergo_email or not self.evergo_password:
            raise EvergoAPIError("Evergo credentials are incomplete.")

        created = 0
        updated = 0
        with requests.Session() as session:
            self._login_session(session=session, timeout=timeout)
            self._sync_catalog(
                session=session,
                timeout=timeout,
                field_name=EvergoOrderFieldValue.FIELD_SITIO,
                url=self.API_SITIOS_URL,
            )
            self._sync_catalog(
                session=session,
                timeout=timeout,
                field_name=EvergoOrderFieldValue.FIELD_INGENIERO,
                url=self.API_INGENIEROS_URL,
            )
            self._sync_catalog(
                session=session,
                timeout=timeout,
                field_name=EvergoOrderFieldValue.FIELD_ESTATUS,
                url=self.API_ORDEN_ESTATUS_URL,
            )

            page = 1
            while True:
                payload = self._request_json(
                    session=session,
                    timeout=timeout,
                    method="GET",
                    url=self.API_ORDERS_URL,
                    params={
                        "page": page,
                        "ingenieroAsignadoId": self.evergo_user_id or "",
                        "numero": "",
                        "conCargador": "",
                        "cliente": "",
                        "from": "",
                        "to": "",
                    },
                )
                data = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(data, list) or not data:
                    break

                for item in data:
                    if not isinstance(item, dict) or not self._is_assigned_to_user(item):
                        continue
                    was_created = self._upsert_order(item)
                    if was_created:
                        created += 1
                    else:
                        updated += 1

                last_page = _to_int(payload.get("last_page")) if isinstance(payload, dict) else None
                current_page = _to_int(payload.get("current_page")) if isinstance(payload, dict) else page
                if last_page and current_page and current_page >= last_page:
                    break
                page += 1

        return created, updated

    def _login_session(self, *, session: requests.Session, timeout: int) -> None:
        """Authenticate a requests session against Evergo."""
        xsrf_token = self._prime_session(session=session, timeout=timeout)
        payload = self._request_json(
            session=session,
            timeout=timeout,
            method="POST",
            url=self.API_LOGIN_URL,
            json={"email": self.evergo_email, "password": self.evergo_password},
            headers=self._build_login_headers(xsrf_token=xsrf_token),
        )
        if isinstance(payload, dict):
            self.apply_login_payload(payload)
            self.last_login_test_at = timezone.now()
            self.save()

    def _request_json(
        self,
        *,
        session: requests.Session,
        timeout: int,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        **request_kwargs: Any,
    ) -> Any:
        """Run an Evergo request and return parsed JSON, raising EvergoAPIError on failure."""
        try:
            response = session.request(
                method=method,
                url=url,
                timeout=timeout,
                headers=headers or {"accept": "application/json"},
                **request_kwargs,
            )
        except requests.RequestException as exc:
            raise EvergoAPIError(f"Unable to connect to Evergo API: {exc}") from exc

        if response.status_code >= 400:
            if response.status_code == 419:
                raise EvergoAPIError(
                    "Evergo request failed with status 419 (CSRF/session validation failed)."
                )
            raise EvergoAPIError(f"Evergo request failed with status {response.status_code}.")

        try:
            return response.json()
        except ValueError as exc:
            raise EvergoAPIError("Evergo API returned non-JSON payload.") from exc

    def _sync_catalog(
        self,
        *,
        session: requests.Session,
        timeout: int,
        field_name: str,
        url: str,
    ) -> None:
        """Persist catalog options so local mappings can evolve with upstream values."""
        payload = self._request_json(
            session=session,
            timeout=timeout,
            method="GET",
            url=url,
        )
        if not isinstance(payload, list):
            return

        for item in payload:
            if not isinstance(item, dict):
                continue
            remote_id = _to_int(item.get("id"))
            remote_name = str(
                item.get("nombre")
                or item.get("name")
                or item.get("user_info")
                or ""
            )
            if remote_id is None or not remote_name:
                continue
            EvergoOrderFieldValue.objects.update_or_create(
                field_name=field_name,
                remote_id=remote_id,
                defaults={
                    "remote_name": remote_name,
                    "raw_payload": item,
                    "last_seen_at": timezone.now(),
                },
            )

    def _is_assigned_to_user(self, payload: dict[str, Any]) -> bool:
        """Check whether the upstream order is assigned to the current Evergo user."""
        if not self.evergo_user_id:
            return True
        direct_technician = _to_int(payload.get("user_tecnico_id"))
        if direct_technician == self.evergo_user_id:
            return True

        installer = payload.get("orden_instalador")
        if not isinstance(installer, dict):
            return False
        engineer_id = _to_int(installer.get("idIngeniero"))
        coordinator_id = _to_int(installer.get("idCoordinador"))
        return self.evergo_user_id in {engineer_id, coordinator_id}

    def _upsert_order(self, payload: dict[str, Any]) -> bool:
        """Create or update an `EvergoOrder` from raw Evergo API data."""
        remote_id = _to_int(payload.get("id"))
        if remote_id is None:
            raise EvergoAPIError("Evergo order payload is missing a valid 'id'.")

        defaults = {
            "user": self,
            "order_number": str(payload.get("numero_orden") or ""),
            "prefix": str(payload.get("prefijo") or ""),
            "suffix": str(payload.get("sufijo") or ""),
            "uuid": _to_int(payload.get("uuid")),
            "scheduled_for": _parse_dt(payload.get("fecha_programada_timestamp"))
            or _parse_dt(payload.get("fecha_programada")),
            "status_id": _to_int(payload.get("idOrdenEstatus")),
            "status_name": _nested_name(payload.get("estatus")),
            "site_id": _to_int(payload.get("idSitio")),
            "site_name": _nested_name(payload.get("sitio")),
            "client_id": _to_int(payload.get("idCliente")),
            "client_name": _nested_name(payload.get("cliente")),
            "assigned_engineer_id": _nested_int(payload.get("orden_instalador"), "idIngeniero"),
            "assigned_engineer_name": _nested_name(_nested_dict(payload.get("orden_instalador"), "ingeniero")),
            "assigned_coordinator_id": _nested_int(payload.get("orden_instalador"), "idCoordinador"),
            "assigned_coordinator_name": _nested_name(_nested_dict(payload.get("orden_instalador"), "coordinador")),
            "has_charger": bool(_to_int(payload.get("has_charger"))),
            "has_vehicle": bool(_to_int(payload.get("has_vehicle"))),
            "raw_payload": payload,
            "source_created_at": _parse_dt(payload.get("created_at")),
            "source_updated_at": _parse_dt(payload.get("updated_at")),
        }
        charge_points = payload.get("cargadores")
        if isinstance(charge_points, list):
            defaults["charger_count"] = len(charge_points)

        order, created = EvergoOrder.objects.update_or_create(
            remote_id=remote_id,
            defaults=defaults,
        )
        order.sync_dynamic_field_values(payload)
        return created


class EvergoOrderFieldValue(models.Model):
    """Known dropdown-like values observed from Evergo catalogs and order payloads."""

    FIELD_SITIO = "sitio"
    FIELD_ESTATUS = "estatus"
    FIELD_INGENIERO = "ingeniero"
    FIELD_PREORDEN_TIPO = "preorden_tipo"
    FIELD_PAYMENT_BY = "payment_by"

    field_name = models.CharField(max_length=64)
    remote_id = models.PositiveIntegerField(null=True, blank=True)
    remote_name = models.CharField(max_length=255)
    local_label = models.CharField(max_length=255, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Evergo Order Field Value"
        verbose_name_plural = "Evergo Order Field Values"
        unique_together = ("field_name", "remote_id")

    def __str__(self) -> str:
        """Return a concise human-readable representation."""
        return f"{self.field_name}: {self.remote_name}"


class EvergoOrder(models.Model):
    """Local cache of Evergo installer/coordinator orders for the authenticated user."""

    user = models.ForeignKey(EvergoUser, on_delete=models.CASCADE, related_name="orders")
    remote_id = models.PositiveIntegerField(unique=True, db_index=True)
    order_number = models.CharField(max_length=64, blank=True)
    prefix = models.CharField(max_length=32, blank=True)
    suffix = models.CharField(max_length=32, blank=True)
    uuid = models.PositiveIntegerField(null=True, blank=True)

    status_id = models.PositiveIntegerField(null=True, blank=True)
    status_name = models.CharField(max_length=255, blank=True)
    site_id = models.PositiveIntegerField(null=True, blank=True)
    site_name = models.CharField(max_length=255, blank=True)
    client_id = models.PositiveIntegerField(null=True, blank=True)
    client_name = models.CharField(max_length=255, blank=True)

    assigned_engineer_id = models.PositiveIntegerField(null=True, blank=True)
    assigned_engineer_name = models.CharField(max_length=255, blank=True)
    assigned_coordinator_id = models.PositiveIntegerField(null=True, blank=True)
    assigned_coordinator_name = models.CharField(max_length=255, blank=True)

    has_charger = models.BooleanField(default=False)
    has_vehicle = models.BooleanField(default=False)
    charger_count = models.PositiveIntegerField(default=0)
    estimated_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    source_updated_at = models.DateTimeField(null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    refreshed_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Evergo Order"
        verbose_name_plural = "Evergo Orders"
        ordering = ("-source_updated_at", "-remote_id")

    def __str__(self) -> str:
        """Return an informative order identifier for admin and logs."""
        if self.order_number:
            return self.order_number
        return f"EvergoOrder#{self.remote_id}"

    def sync_dynamic_field_values(self, payload: dict[str, Any]) -> None:
        """Track dropdown-like field values as they appear in incoming order payloads."""
        mapping = (
            (EvergoOrderFieldValue.FIELD_SITIO, payload.get("sitio")),
            (EvergoOrderFieldValue.FIELD_ESTATUS, payload.get("estatus")),
            (EvergoOrderFieldValue.FIELD_PREORDEN_TIPO, payload.get("preorden_tipo")),
        )
        for field_name, source in mapping:
            if not isinstance(source, dict):
                continue
            remote_id = _to_int(source.get("id"))
            remote_name = _nested_name(source)
            if remote_id is None or not remote_name:
                continue
            EvergoOrderFieldValue.objects.update_or_create(
                field_name=field_name,
                remote_id=remote_id,
                defaults={
                    "remote_name": remote_name,
                    "raw_payload": source,
                    "last_seen_at": timezone.now(),
                },
            )

        payment_by = str(payload.get("paymentBy") or "").strip()
        if payment_by:
            EvergoOrderFieldValue.objects.update_or_create(
                field_name=EvergoOrderFieldValue.FIELD_PAYMENT_BY,
                remote_id=None,
                remote_name=payment_by,
                defaults={"raw_payload": {"value": payment_by}, "last_seen_at": timezone.now()},
            )

        amount = payload.get("monto")
        try:
            self.estimated_amount = Decimal(str(amount)) if amount not in (None, "") else None
        except (InvalidOperation, ValueError):
            self.estimated_amount = None
        self.save(update_fields=["estimated_amount", "refreshed_at"])


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


def _nested_dict(value: Any, key: str) -> dict[str, Any]:
    """Safely return a dictionary from a nested object lookup."""
    if not isinstance(value, dict):
        return {}
    nested = value.get(key)
    if not isinstance(nested, dict):
        return {}
    return nested


def _nested_int(value: Any, key: str) -> int | None:
    """Safely coerce a nested dictionary integer field."""
    if not isinstance(value, dict):
        return None
    return _to_int(value.get(key))


def _nested_name(value: Any) -> str:
    """Extract a user-facing name from dictionary payloads."""
    if not isinstance(value, dict):
        return ""
    return str(value.get("nombre") or value.get("name") or "")
