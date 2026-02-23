"""Models for Evergo credential, profile, and order synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import re
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
    CUSTOMER_QUERY_DELIMITERS = re.compile(r"[,;\|\n\r\t]+")
    SALES_ORDER_PATTERN = re.compile(r"\b[A-Za-z]{1,4}\d{3,8}\b")

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
                    self._upsert_customer_from_order(item)
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

    def load_customers_from_queries(
        self,
        *,
        raw_queries: str,
        timeout: int = 20,
    ) -> dict[str, Any]:
        """Load customers/orders from SO codes and customer name fragments.

        Returns a summary payload with loaded records and unresolved inputs.
        """
        if not self.evergo_email or not self.evergo_password:
            raise EvergoAPIError("Evergo credentials are incomplete.")

        sales_orders, customer_names = self.parse_customer_queries(raw_queries=raw_queries)
        unresolved: list[str] = []
        customers_loaded = 0
        orders_created = 0
        orders_updated = 0
        placeholders_created = 0

        with requests.Session() as session:
            self._login_session(session=session, timeout=timeout)

            for so_number in sales_orders:
                order_payloads = self._fetch_orders_for_lookup(
                    session=session,
                    timeout=timeout,
                    so_number=so_number,
                )
                if not order_payloads:
                    self._ensure_placeholder_order(so_number=so_number)
                    placeholders_created += 1
                    unresolved.append(so_number)
                    continue

                customers_inc, created_inc, updated_inc = self._process_order_payloads(order_payloads)
                customers_loaded += customers_inc
                orders_created += created_inc
                orders_updated += updated_inc

            for customer_name in customer_names:
                order_payloads = self._fetch_orders_for_lookup(
                    session=session,
                    timeout=timeout,
                    customer_name=customer_name,
                )
                if not order_payloads:
                    unresolved.append(customer_name)
                    continue

                customers_inc, created_inc, updated_inc = self._process_order_payloads(order_payloads)
                customers_loaded += customers_inc
                orders_created += created_inc
                orders_updated += updated_inc

        return {
            "sales_orders": sales_orders,
            "customer_names": customer_names,
            "customers_loaded": customers_loaded,
            "orders_created": orders_created,
            "orders_updated": orders_updated,
            "placeholders_created": placeholders_created,
            "unresolved": unresolved,
        }

    @classmethod
    def parse_customer_queries(cls, *, raw_queries: str) -> tuple[list[str], list[str]]:
        """Parse free-form admin input into SO numbers and customer-name queries."""
        source = (raw_queries or "").strip()
        if not source:
            return [], []

        sales_orders: list[str] = []
        seen_sales_orders: set[str] = set()
        for token in cls.SALES_ORDER_PATTERN.findall(source):
            normalized = token.strip().upper()
            if normalized and normalized not in seen_sales_orders:
                seen_sales_orders.add(normalized)
                sales_orders.append(normalized)

        names_blob = source
        for so_number in sales_orders:
            names_blob = re.sub(rf"\b{re.escape(so_number)}\b", " ", names_blob, flags=re.IGNORECASE)

        customer_names: list[str] = []
        seen_names: set[str] = set()
        for chunk in cls.CUSTOMER_QUERY_DELIMITERS.split(names_blob):
            normalized = " ".join(chunk.split())
            if len(normalized) < 2:
                continue
            if normalized.lower() in seen_names:
                continue
            seen_names.add(normalized.lower())
            customer_names.append(normalized)

        return sales_orders, customer_names

    def _fetch_orders_for_lookup(
        self,
        *,
        session: requests.Session,
        timeout: int,
        so_number: str | None = None,
        customer_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch all order payload rows for one SO number or customer name query."""
        page = 1
        rows: list[dict[str, Any]] = []
        while True:
            payload = self._request_json(
                session=session,
                timeout=timeout,
                method="GET",
                url=self.API_ORDERS_URL,
                params={
                    "page": page,
                    "ingenieroAsignadoId": self.evergo_user_id or "",
                    "numero": so_number or "",
                    "conCargador": "",
                    "cliente": customer_name or "",
                    "from": "",
                    "to": "",
                },
            )
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list) or not data:
                break
            for item in data:
                if isinstance(item, dict) and self._is_assigned_to_user(item):
                    rows.append(item)

            last_page = _to_int(payload.get("last_page")) if isinstance(payload, dict) else None
            current_page = _to_int(payload.get("current_page")) if isinstance(payload, dict) else page
            if last_page and current_page and current_page >= last_page:
                break
            page += 1
        return rows

    def _process_order_payloads(self, order_payloads: list[dict[str, Any]]) -> tuple[int, int, int]:
        """Upsert orders/customers and return created/updated/customer counters."""
        customers_loaded = 0
        orders_created = 0
        orders_updated = 0

        for payload in order_payloads:
            was_created = self._upsert_order(payload)
            customer_created = self._upsert_customer_from_order(payload)
            customers_loaded += int(customer_created)
            if was_created:
                orders_created += 1
            else:
                orders_updated += 1

        return customers_loaded, orders_created, orders_updated

    def _ensure_placeholder_order(self, *, so_number: str) -> EvergoOrder:
        """Create/update a provisional local order row when SO is not found upstream."""
        remote_id = _placeholder_remote_id(order_number=so_number)
        order, _ = EvergoOrder.objects.update_or_create(
            remote_id=remote_id,
            defaults={
                "user": self,
                "order_number": so_number,
                "validation_state": EvergoOrder.VALIDATION_STATE_PLACEHOLDER,
                "raw_payload": {"placeholder": True, "order_number": so_number},
            },
        )
        return order

    def _upsert_customer_from_order(self, payload: dict[str, Any]) -> bool:
        """Create/update a customer snapshot derived from one order payload."""
        customer_payload = payload.get("cliente")
        install_payload = payload.get("orden_instalacion")
        if not isinstance(customer_payload, dict) and not isinstance(install_payload, dict):
            return False

        customer_id = _to_int(customer_payload.get("id")) if isinstance(customer_payload, dict) else None
        customer_name = ""
        if isinstance(customer_payload, dict):
            customer_name = _nested_name(customer_payload)
        if not customer_name and isinstance(install_payload, dict):
            customer_name = str(install_payload.get("nombre_completo") or "").strip()

        latest_so = str(payload.get("numero_orden") or "").strip()
        phone = ""
        address = ""
        if isinstance(install_payload, dict):
            phone = str(
                install_payload.get("telefono_celular")
                or install_payload.get("telefono_fijo1")
                or install_payload.get("telefono_fijo2")
                or ""
            ).strip()
            address = str(
                install_payload.get("direccion")
                or " ".join(
                    filter(
                        None,
                        [
                            install_payload.get("calle"),
                            install_payload.get("num_ext"),
                            install_payload.get("num_int"),
                            install_payload.get("colonia"),
                            install_payload.get("municipio"),
                            install_payload.get("ciudad"),
                            install_payload.get("codigo_postal"),
                        ],
                    )
                )
                or ""
            ).strip()

        order = None
        remote_order_id = _to_int(payload.get("id"))
        if remote_order_id is not None:
            order = EvergoOrder.objects.filter(remote_id=remote_order_id).first()

        defaults = {
            "name": customer_name,
            "email": str(customer_payload.get("email") or "") if isinstance(customer_payload, dict) else "",
            "phone_number": phone,
            "address": address,
            "latest_so": latest_so,
            "latest_order": order,
            "latest_order_updated_at": _parse_dt(payload.get("updated_at")),
            "raw_payload": {
                "cliente": customer_payload if isinstance(customer_payload, dict) else {},
                "orden_instalacion": install_payload if isinstance(install_payload, dict) else {},
            },
        }

        if customer_id is not None:
            _, created = EvergoCustomer.objects.update_or_create(
                user=self,
                remote_id=customer_id,
                defaults=defaults,
            )
            return created

        if not customer_name:
            return False

        existing_customer = (
            EvergoCustomer.objects.filter(user=self, remote_id__isnull=True, name=customer_name)
            .order_by("id")
            .first()
        )
        if existing_customer is not None:
            for field_name, value in defaults.items():
                setattr(existing_customer, field_name, value)
            existing_customer.save(update_fields=[*defaults.keys(), "refreshed_at"])
            return False

        EvergoCustomer.objects.create(user=self, remote_id=None, **defaults)
        return True

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
            "validation_state": EvergoOrder.VALIDATION_STATE_VALIDATED,
        }
        charge_points = payload.get("cargadores")
        if isinstance(charge_points, list):
            defaults["charger_count"] = len(charge_points)

        order, created = EvergoOrder.objects.update_or_create(
            remote_id=remote_id,
            defaults=defaults,
        )

        order_number = defaults["order_number"].strip().upper()
        if order_number:
            placeholder_remote_id = _placeholder_remote_id(order_number=order_number)
            (
                EvergoOrder.objects.filter(remote_id=placeholder_remote_id)
                .exclude(pk=order.pk)
                .delete()
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

    VALIDATION_STATE_VALIDATED = "validated"
    VALIDATION_STATE_PLACEHOLDER = "placeholder"
    VALIDATION_STATE_CHOICES = (
        (VALIDATION_STATE_VALIDATED, "Validated in Evergo"),
        (VALIDATION_STATE_PLACEHOLDER, "Temporary placeholder"),
    )

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
    validation_state = models.CharField(
        max_length=20,
        choices=VALIDATION_STATE_CHOICES,
        default=VALIDATION_STATE_VALIDATED,
    )
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


class EvergoCustomer(models.Model):
    """Local cache of customer info sourced from Evergo sales-order payloads."""

    user = models.ForeignKey(EvergoUser, on_delete=models.CASCADE, related_name="customers")
    remote_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    phone_number = models.CharField(max_length=64, blank=True)
    address = models.CharField(max_length=512, blank=True)
    latest_so = models.CharField(max_length=64, blank=True)
    latest_order = models.ForeignKey(
        EvergoOrder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customers",
    )
    latest_order_updated_at = models.DateTimeField(null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    refreshed_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Evergo Customer"
        verbose_name_plural = "Evergo Customers"
        unique_together = (("user", "remote_id"),)
        ordering = ("-latest_order_updated_at", "name")

    def __str__(self) -> str:
        """Return a concise customer label."""
        if self.latest_so:
            return f"{self.name} ({self.latest_so})"
        return self.name


def _to_int(value: Any) -> int | None:
    """Convert loosely typed API integers into local integer fields."""
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _placeholder_remote_id(*, order_number: str) -> int:
    """Build a deterministic positive integer to persist a provisional SO row."""
    # Reserve [1_500_000_000, 2_000_000_000) for placeholders to avoid collisions
    # with current real Evergo IDs while keeping deterministic order-number mapping.
    digest = hashlib.sha256(order_number.strip().upper().encode("utf-8")).hexdigest()[:12]
    return 1_500_000_000 + (int(digest, 16) % 500_000_000)


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
