"""Evergo user model and synchronization operations."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import unquote, urlsplit
import uuid

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone
from encrypted_model_fields.fields import EncryptedCharField, EncryptedTextField

from apps.users.models import Profile

from apps.evergo.exceptions import EvergoAPIError, EvergoPhaseSubmissionError
from .customer import EvergoCustomer
from .order import EvergoOrder, EvergoOrderFieldValue
from .parsing import (
    first_dict,
    nested_dict,
    nested_int,
    nested_name,
    parse_dt,
    placeholder_remote_id,
    to_int,
)


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

    API_ORDER_DETAIL_URL_TEMPLATE = getattr(
        settings,
        "EVERGO_API_ORDER_DETAIL_URL_TEMPLATE",
        "https://portal-backend.evergo.com/api/mex/v1/ordenes/{order_id}",
    )
    API_SAVE_VISITA_URL_TEMPLATE = getattr(
        settings,
        "EVERGO_API_SAVE_VISITA_URL_TEMPLATE",
        "https://portal-backend.evergo.com/api/mex/v1/reportes/ordenes/{order_id}/save-visita-tecnica",
    )
    API_ASSIGN_URL_TEMPLATE = getattr(
        settings,
        "EVERGO_API_ASSIGN_URL_TEMPLATE",
        "https://portal-backend.evergo.com/api/mex/v1/ordenes/{order_id}/asignar-tecnico",
    )
    API_REPORT_INSTALL_URL_TEMPLATE = getattr(
        settings,
        "EVERGO_API_REPORT_INSTALL_URL_TEMPLATE",
        "https://portal-backend.evergo.com/api/mex/v1/reportes/ordenes/{order_id}/save-reporte-instalacion",
    )
    API_MONTAJE_QUESTIONS_URL_TEMPLATE = getattr(
        settings,
        "EVERGO_API_MONTAJE_QUESTIONS_URL_TEMPLATE",
        "https://portal-backend.evergo.com/api/mex/v1/reportes/ordenes/{order_id}/montaje-conexion/cuestionario-preguntas",
    )
    API_SAVE_MONTAJE_URL_TEMPLATE = getattr(
        settings,
        "EVERGO_API_SAVE_MONTAJE_URL_TEMPLATE",
        "https://portal-backend.evergo.com/api/mex/v1/reportes/ordenes/{order_id}/save-montaje-conexion",
    )

    profile_fields = ("evergo_email", "evergo_password")

    evergo_email = models.EmailField()
    evergo_password = EncryptedCharField(max_length=255, blank=True)
    dashboard_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

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
        verbose_name = "Evergo Contractor"
        verbose_name_plural = "Evergo Contractors"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(evergo_email=""),
                name="evergo_evergouser_email_non_empty",
            ),
        ]

    def __str__(self) -> str:
        """Return a readable identifier for admin lists."""
        return self.name or self.email or self.evergo_email or f"EvergoUser#{self.pk}"

    def get_dashboard_url(self) -> str:
        """Return the secure public dashboard URL for this Evergo profile."""
        return reverse("evergo:my-dashboard", kwargs={"token": self.dashboard_token})

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
        self.evergo_user_id = to_int(payload.get("id"))
        self.name = str(payload.get("name") or "")
        self.email = str(payload.get("email") or "")
        self.two_fa_enabled = bool(to_int(payload.get("two_fa_enabled")))
        self.two_fa_authenticated = bool(to_int(payload.get("two_fa_authenticated")))
        self.two_factor_secret = str(payload.get("two_factor_secret") or "")
        self.two_factor_recovery_codes = str(
            payload.get("two_factor_recovery_codes") or ""
        )
        self.two_factor_confirmed_at = parse_dt(payload.get("two_factor_confirmed_at"))

        subempresa = first_dict(payload.get("subempresas"))
        self.subempresa_id = to_int(subempresa.get("id"))
        self.subempresa_name = str(subempresa.get("nombre") or "")
        self.empresa_id = to_int(subempresa.get("idInstalaEmpresa"))
        self.empresa_name = str(subempresa.get("empresa") or "")

        self.evergo_created_at = parse_dt(payload.get("created_at"))
        self.evergo_updated_at = parse_dt(payload.get("updated_at"))

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
                    was_created, _ = self._upsert_order(item)
                    self._upsert_customer_from_order(item)
                    if was_created:
                        created += 1
                    else:
                        updated += 1

                last_page = to_int(payload.get("last_page")) if isinstance(payload, dict) else None
                current_page = to_int(payload.get("current_page")) if isinstance(payload, dict) else page
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
        load_all_customers = not sales_orders and not customer_names
        unresolved: list[str] = []
        customers_loaded = 0
        orders_created = 0
        orders_updated = 0
        placeholders_created = 0
        loaded_customer_ids: set[int] = set()
        loaded_order_ids: set[int] = set()

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

                customers_inc, created_inc, updated_inc, customer_ids, order_ids = self._process_order_payloads(
                    order_payloads
                )
                customers_loaded += customers_inc
                orders_created += created_inc
                orders_updated += updated_inc
                loaded_customer_ids.update(customer_ids)
                loaded_order_ids.update(order_ids)

            lookup_names = [""] if load_all_customers else customer_names
            for customer_name in lookup_names:
                order_payloads = self._fetch_orders_for_lookup(
                    session=session,
                    timeout=timeout,
                    customer_name=customer_name,
                )
                if not order_payloads:
                    if customer_name:
                        unresolved.append(customer_name)
                    continue

                customers_inc, created_inc, updated_inc, customer_ids, order_ids = self._process_order_payloads(
                    order_payloads
                )
                customers_loaded += customers_inc
                orders_created += created_inc
                orders_updated += updated_inc
                loaded_customer_ids.update(customer_ids)
                loaded_order_ids.update(order_ids)

        return {
            "sales_orders": sales_orders,
            "customer_names": customer_names,
            "customers_loaded": customers_loaded,
            "orders_created": orders_created,
            "orders_updated": orders_updated,
            "placeholders_created": placeholders_created,
            "unresolved": unresolved,
            "loaded_customer_ids": sorted(loaded_customer_ids),
            "loaded_order_ids": sorted(loaded_order_ids),
        }


    def fetch_order_detail(self, *, order_id: int, timeout: int = 20) -> dict[str, Any]:
        """Fetch the latest order payload for a public tracking session."""
        with requests.Session() as session:
            self._login_session(session=session, timeout=timeout)
            return self._request_json(
                session=session,
                timeout=timeout,
                method="GET",
                url=self.API_ORDER_DETAIL_URL_TEMPLATE.format(order_id=order_id),
            )

    def reload_order_from_remote(self, *, order: EvergoOrder, timeout: int = 20) -> EvergoOrder:
        """Clear one cached order snapshot and fetch the latest data from Evergo."""
        if order.user_id != self.pk:
            raise EvergoAPIError("Order does not belong to this Evergo profile.")
        if order.remote_id is None:
            raise EvergoAPIError("Order has no remote ID and cannot be reloaded from Evergo.")

        payload = self.fetch_order_detail(order_id=order.remote_id, timeout=timeout)
        normalized_payload = self._extract_order_payload(payload)
        if normalized_payload is None:
            raise EvergoAPIError("Evergo order detail response did not include a valid order payload.")

        remote_id = order.remote_id
        with transaction.atomic():
            order.delete()
            self._upsert_order(normalized_payload)
            self._upsert_customer_from_order(normalized_payload)
        return EvergoOrder.objects.get(user=self, remote_id=remote_id)

    def reload_customer_from_remote(self, *, customer: EvergoCustomer, timeout: int = 20) -> EvergoCustomer:
        """Clear one cached customer snapshot and fetch fresh payload data from Evergo."""
        if customer.user_id != self.pk:
            raise EvergoAPIError("Customer does not belong to this Evergo profile.")

        stale_customer_pk = customer.pk
        stale_customer_name = customer.name
        stale_customer_remote_id = customer.remote_id
        stale_latest_order = customer.latest_order

        def _detach_stale_customer() -> None:
            """Free unique customer keys so reload creates a replacement snapshot row."""

            updates: list[str] = []
            if customer.remote_id is not None:
                customer.remote_id = None
                updates.append("remote_id")
            if customer.latest_order_id is not None:
                customer.latest_order = None
                updates.append("latest_order")
            if stale_customer_name and customer.name == stale_customer_name:
                customer.name = f"__stale_customer__{stale_customer_pk}"
                updates.append("name")
            if updates:
                customer.save(update_fields=updates)

        def _delete_stale_customer() -> None:
            """Delete the stale customer snapshot once a replacement has been created."""

            EvergoCustomer.objects.filter(pk=stale_customer_pk).delete()

        if stale_latest_order and stale_latest_order.remote_id is not None:
            with transaction.atomic():
                _detach_stale_customer()
                refreshed_order = self.reload_order_from_remote(order=stale_latest_order, timeout=timeout)
                refreshed_customer = (
                    EvergoCustomer.objects.filter(user=self, latest_order=refreshed_order)
                    .exclude(pk=stale_customer_pk)
                    .order_by("pk")
                    .first()
                )
                if refreshed_customer is None:
                    raise EvergoAPIError(
                        "Reload succeeded but no replacement customer snapshot was linked to the refreshed order."
                    )
                _delete_stale_customer()
                return refreshed_customer

        queries = [token for token in [customer.latest_so, customer.name] if token]
        if not queries:
            raise EvergoAPIError("Customer has no lookup data (SO or name) for Evergo reload.")
        with transaction.atomic():
            _detach_stale_customer()
            summary = self.load_customers_from_queries(raw_queries="\n".join(queries), timeout=timeout)

            refreshed_candidates = EvergoCustomer.objects.filter(user=self).exclude(pk=stale_customer_pk)
            refreshed_customer = None
            if stale_customer_remote_id is not None:
                refreshed_customer = refreshed_candidates.filter(
                    remote_id=stale_customer_remote_id
                ).order_by("-latest_order_updated_at", "pk").first()
            if refreshed_customer is None:
                refreshed_customer = refreshed_candidates.filter(
                    name__iexact=stale_customer_name
                ).order_by("-latest_order_updated_at", "pk").first()
            if refreshed_customer is None:
                if summary["customers_loaded"] <= 0:
                    raise EvergoAPIError("Evergo did not return data for the selected customer.")
                raise EvergoAPIError("Reload completed but refreshed customer could not be located locally.")
            _delete_stale_customer()
            return refreshed_customer

    @staticmethod
    def _extract_order_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
        """Normalize Evergo order-detail responses into one order payload dictionary."""
        if not isinstance(payload, dict):
            return None
        if isinstance(payload.get("data"), dict):
            return payload["data"]
        if isinstance(payload.get("orden"), dict):
            return payload["orden"]
        if to_int(payload.get("id")) is not None:
            return payload
        return None

    def fetch_charger_brand_options(self, *, timeout: int = 20) -> list[str]:
        """Build charger-brand options from the user's currently assigned orders."""
        seen: set[str] = set()
        for payload in EvergoOrder.objects.filter(user=self).values_list("raw_payload", flat=True):
            if not isinstance(payload, dict):
                continue
            for charger in payload.get("cargadores") or []:
                if not isinstance(charger, dict):
                    continue
                brand = str(charger.get("nombre") or charger.get("modelo") or "").strip()
                if brand:
                    seen.add(brand)
        return sorted(seen)

    def submit_tracking_phase_one(
        self,
        *,
        order_id: int,
        payload: dict[str, Any],
        files: dict[str, Any],
        timeout: int = 30,
        step_completion: dict[str, bool] | None = None,
    ) -> dict[str, Any]:
        """Submit tracking data progressively and complete each step only when requirements are met."""
        with requests.Session() as session:
            self._login_session(session=session, timeout=timeout)
            order_payload = self._request_json(
                session=session,
                timeout=timeout,
                method="GET",
                url=self.API_ORDER_DETAIL_URL_TEMPLATE.format(order_id=order_id),
            )

            completion = step_completion or {"visita": True, "assign": True, "install": True, "montage": True}
            completed_steps = 0

            visita_response = None
            if completion.get("visita"):
                self._rewind_uploads(files.values())
                visita_response = session.post(
                    self.API_SAVE_VISITA_URL_TEMPLATE.format(order_id=order_id),
                    data=payload,
                    files=files,
                    timeout=timeout,
                )
                if visita_response.status_code >= 400:
                    raise EvergoPhaseSubmissionError("Visita Tecnica", visita_response.status_code, 0)
                completed_steps += 1

            assign_response = None
            if completion.get("assign"):
                order_installer = order_payload.get("orden_instalador") if isinstance(order_payload, dict) else {}
                assign_response = session.post(
                    self.API_ASSIGN_URL_TEMPLATE.format(order_id=order_id),
                    json={
                        "user_tecnico_id": order_payload.get("user_tecnico_id") or self.evergo_user_id,
                        "fecha_programada": payload.get("fecha_visita"),
                        "crew": (order_installer or {}).get("idSubempresa") or 20,
                        "type": "Installation",
                        "crewPeople": [(order_installer or {}).get("idIngeniero") or self.evergo_user_id],
                        "reassignment_reason_id": None,
                        "reason_comment": "",
                        "tag_requiered": False,
                    },
                    timeout=timeout,
                )
                if assign_response.status_code >= 400:
                    raise EvergoPhaseSubmissionError("Asignar técnico", assign_response.status_code, completed_steps)
                completed_steps += 1

            install_response = None
            if completion.get("install"):
                self._rewind_uploads(files.values())
                install_response = session.post(
                    self.API_REPORT_INSTALL_URL_TEMPLATE.format(order_id=order_id),
                    data=payload,
                    files=files,
                    timeout=timeout,
                )
                if install_response.status_code >= 400:
                    raise EvergoPhaseSubmissionError("Reporte de Instalacion", install_response.status_code, completed_steps)
                completed_steps += 1

            montage_response = None
            if completion.get("montage"):
                montage_questions_payload = self._request_json(
                    session=session,
                    timeout=timeout,
                    method="GET",
                    url=self.API_MONTAJE_QUESTIONS_URL_TEMPLATE.format(order_id=order_id),
                )
                montaje_data, montaje_files = self._build_montaje_submission(
                    questionnaire_payload=montage_questions_payload,
                    payload=payload,
                    files=files,
                )
                self._rewind_uploads([file_obj for _, file_obj in montaje_files])
                montage_response = session.post(
                    self.API_SAVE_MONTAJE_URL_TEMPLATE.format(order_id=order_id),
                    data=montaje_data,
                    files=montaje_files,
                    timeout=timeout,
                )
                if montage_response.status_code >= 400:
                    raise EvergoPhaseSubmissionError("Montaje-Conexión", montage_response.status_code, completed_steps)
                completed_steps += 1

            return {
                "order_payload": order_payload,
                "phase_1_status": visita_response.status_code if visita_response is not None else None,
                "phase_1_payload": visita_response.json() if visita_response is not None and visita_response.content else {},
                "assign_status": assign_response.status_code if assign_response is not None else None,
                "assign_payload": assign_response.json() if assign_response is not None and assign_response.content else {},
                "install_status": install_response.status_code if install_response is not None else None,
                "install_payload": install_response.json() if install_response is not None and install_response.content else {},
                "montage_status": montage_response.status_code if montage_response is not None else None,
                "montage_payload": montage_response.json() if montage_response is not None and montage_response.content else {},
                "completed_steps": completed_steps,
            }

    def _build_montaje_submission(
        self,
        *,
        questionnaire_payload: dict[str, Any],
        payload: dict[str, Any],
        files: dict[str, Any],
    ) -> tuple[dict[str, Any], list[tuple[str, Any]]]:
        """Build dynamic montaje payload from questionnaire metadata and tracked values."""
        preguntas = questionnaire_payload.get("preguntas") if isinstance(questionnaire_payload, dict) else []
        if not isinstance(preguntas, list):
            raise EvergoAPIError("Montaje questionnaire payload is invalid.")

        montaje_data: dict[str, Any] = {"reporte": questionnaire_payload.get("reporte")}
        montaje_files: list[tuple[str, Any]] = []

        value_map = {
            20: payload.get("programacion_cargador_instalacion") or payload.get("programacion_cargador"),
            1: payload.get("kit_cfe"),
            28: payload.get("metraje_visita_tecnica"),
            21: payload.get("prueba_carga"),
            16: payload.get("voltaje_fase_fase"),
            17: payload.get("voltaje_fase_tierra"),
            18: payload.get("voltaje_fase_neutro"),
            19: payload.get("voltaje_neutro_tierra"),
            11: payload.get("capacidad_itm_principal"),
            9: payload.get("obra_civil"),
        }
        file_map = {
            26: files.get("foto_panoramica_estacion"),
            27: files.get("foto_numero_serie_cargador"),
            4: files.get("foto_voltaje_fase_fase"),
            5: files.get("foto_voltaje_fase_neutro"),
            6: files.get("foto_voltaje_fase_tierra"),
            7: files.get("foto_voltaje_neutro_tierra"),
            12: files.get("foto_interruptor_principal"),
            23: files.get("foto_interruptor_instalado"),
            25: files.get("foto_conexion_cargador"),
            22: files.get("foto_preparacion_cfe"),
            24: files.get("foto_hoja_reporte_instalacion"),
        }

        for index, pregunta_layout in enumerate(preguntas):
            if not isinstance(pregunta_layout, dict):
                continue
            pregunta_id = to_int(pregunta_layout.get("pregunta_id"))
            prefix = f"preguntas[{index}]"
            for key in (
                "id",
                "reporte_layout_id",
                "pregunta_id",
                "descripcion",
                "seccion",
                "web_only",
                "requerido",
                "orden",
                "created_by",
                "created_at",
                "updated_at",
                "deleted_at",
            ):
                if key in pregunta_layout:
                    montaje_data[f"{prefix}[{key}]"] = pregunta_layout.get(key)

            pregunta_meta = pregunta_layout.get("pregunta")
            if isinstance(pregunta_meta, dict):
                for key, value in pregunta_meta.items():
                    if key == "opciones" and isinstance(value, list):
                        for op_idx, option in enumerate(value):
                            if not isinstance(option, dict):
                                continue
                            for option_key, option_value in option.items():
                                montaje_data[
                                    f"{prefix}[pregunta][opciones][{op_idx}][{option_key}]"
                                ] = option_value
                    else:
                        montaje_data[f"{prefix}[pregunta][{key}]"] = value

            tipo = ""
            if isinstance(pregunta_meta, dict):
                tipo = str(pregunta_meta.get("tipo") or "")

            if tipo == "archivo":
                file_value = file_map.get(pregunta_id)
                if file_value is None:
                    continue
                montaje_files.append((f"{prefix}[respuesta][0]", file_value))
            else:
                montage_value = value_map.get(pregunta_id)
                montaje_data[f"{prefix}[respuesta]"] = "" if montage_value is None else montage_value

        montaje_data["close_reporte"] = "true"
        return montaje_data, montaje_files

    @staticmethod
    def _rewind_uploads(file_objects: Any) -> None:
        """Reset uploaded file pointers so each API call sends complete file contents."""
        for file_obj in file_objects:
            seek = getattr(file_obj, "seek", None)
            if callable(seek):
                seek(0)


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

            last_page = to_int(payload.get("last_page")) if isinstance(payload, dict) else None
            current_page = to_int(payload.get("current_page")) if isinstance(payload, dict) else page
            if last_page and current_page and current_page >= last_page:
                break
            page += 1
        return rows

    def _process_order_payloads(
        self, order_payloads: list[dict[str, Any]]
    ) -> tuple[int, int, int, set[int], set[int]]:
        """Upsert orders/customers and return counters plus loaded record IDs."""
        customers_loaded = 0
        orders_created = 0
        orders_updated = 0
        loaded_customer_ids: set[int] = set()
        loaded_order_ids: set[int] = set()

        for payload in order_payloads:
            was_created, order = self._upsert_order(payload)
            customer_created, customer = self._upsert_customer_from_order(payload)
            loaded_order_ids.add(order.pk)
            if customer is not None:
                loaded_customer_ids.add(customer.pk)

            customers_loaded += int(customer_created)
            if was_created:
                orders_created += 1
            else:
                orders_updated += 1

        return customers_loaded, orders_created, orders_updated, loaded_customer_ids, loaded_order_ids

    def _ensure_placeholder_order(self, *, so_number: str) -> EvergoOrder:
        """Create/update a provisional local order row when SO is not found upstream."""
        remote_id = placeholder_remote_id(order_number=so_number)
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

    def _upsert_customer_from_order(self, payload: dict[str, Any]) -> tuple[bool, EvergoCustomer | None]:
        """Create/update a customer snapshot derived from one order payload."""
        customer_payload = payload.get("cliente")
        install_payload = payload.get("orden_instalacion")
        if not isinstance(customer_payload, dict) and not isinstance(install_payload, dict):
            return False, None

        customer_id = to_int(customer_payload.get("id")) if isinstance(customer_payload, dict) else None
        customer_name = ""
        if isinstance(customer_payload, dict):
            customer_name = nested_name(customer_payload)
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
            municipio = str(install_payload.get("municipio") or "").strip()
            ciudad = str(install_payload.get("ciudad") or "").strip()
            locality = municipio or ciudad
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
                            locality,
                            install_payload.get("codigo_postal"),
                        ],
                    )
                )
                or ""
            ).strip()

        order = None
        remote_order_id = to_int(payload.get("id"))
        if remote_order_id is not None:
            order = EvergoOrder.objects.filter(remote_id=remote_order_id).first()

        defaults = {
            "name": customer_name,
            "email": str(customer_payload.get("email") or "") if isinstance(customer_payload, dict) else "",
            "phone_number": phone,
            "address": address,
            "latest_so": latest_so,
            "latest_order": order,
            "latest_order_updated_at": parse_dt(payload.get("updated_at")),
            "raw_payload": {
                "cliente": customer_payload if isinstance(customer_payload, dict) else {},
                "orden_instalacion": install_payload if isinstance(install_payload, dict) else {},
            },
        }

        if customer_id is not None:
            customer, created = EvergoCustomer.objects.update_or_create(
                user=self,
                remote_id=customer_id,
                defaults=defaults,
            )
            return created, customer

        if not customer_name:
            return False, None

        existing_customer = (
            EvergoCustomer.objects.filter(user=self, remote_id__isnull=True, name=customer_name)
            .order_by("id")
            .first()
        )
        if existing_customer is not None:
            for field_name, value in defaults.items():
                setattr(existing_customer, field_name, value)
            existing_customer.save(update_fields=[*defaults.keys(), "refreshed_at"])
            return False, existing_customer

        customer = EvergoCustomer.objects.create(user=self, remote_id=None, **defaults)
        return True, customer

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
            remote_id = to_int(item.get("id"))
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
        direct_technician = to_int(payload.get("user_tecnico_id"))
        if direct_technician == self.evergo_user_id:
            return True

        installer = payload.get("orden_instalador")
        if not isinstance(installer, dict):
            return False
        engineer_id = to_int(installer.get("idIngeniero"))
        coordinator_id = to_int(installer.get("idCoordinador"))
        return self.evergo_user_id in {engineer_id, coordinator_id}

    def _upsert_order(self, payload: dict[str, Any]) -> tuple[bool, EvergoOrder]:
        """Create or update an `EvergoOrder` from raw Evergo API data."""
        remote_id = to_int(payload.get("id"))
        if remote_id is None:
            raise EvergoAPIError("Evergo order payload is missing a valid 'id'.")

        installation_data = payload.get("orden_instalacion")
        if not isinstance(installation_data, dict):
            installation_data = {}

        state_payload = payload.get("estado")
        last_contact = (
            parse_dt(payload.get("last_contact_at"))
            or parse_dt(payload.get("last_comment_at"))
            or parse_dt(payload.get("fecha_ultimo_contacto"))
            or parse_dt(payload.get("fecha_ultimo_comentario"))
        )

        defaults = {
            "user": self,
            "order_number": str(payload.get("numero_orden") or ""),
            "prefix": str(payload.get("prefijo") or ""),
            "suffix": str(payload.get("sufijo") or ""),
            "uuid": to_int(payload.get("uuid")),
            "scheduled_for": parse_dt(payload.get("fecha_programada_timestamp"))
            or parse_dt(payload.get("fecha_programada")),
            "status_id": to_int(payload.get("idOrdenEstatus")),
            "status_name": nested_name(payload.get("estatus")),
            "site_id": to_int(payload.get("idSitio")),
            "site_name": nested_name(payload.get("sitio")),
            "client_id": to_int(payload.get("idCliente")),
            "client_name": nested_name(payload.get("cliente")),
            "phone_primary": str(
                installation_data.get("telefono_celular")
                or installation_data.get("telefono_fijo1")
                or ""
            ).strip(),
            "phone_secondary": str(
                installation_data.get("telefono_fijo1")
                or installation_data.get("telefono_fijo2")
                or ""
            ).strip(),
            "address_street": str(installation_data.get("calle") or "").strip(),
            "address_num_ext": str(installation_data.get("num_ext") or "").strip(),
            "address_num_int": str(installation_data.get("num_int") or "").strip(),
            "address_between_streets": str(installation_data.get("entre_calles") or "").strip(),
            "address_neighborhood": str(installation_data.get("colonia") or "").strip(),
            "address_municipality": str(installation_data.get("municipio") or "").strip(),
            "address_city": str(installation_data.get("ciudad") or "").strip(),
            "address_state": nested_name(state_payload)
            or str(installation_data.get("estado") or "").strip(),
            "address_postal_code": str(installation_data.get("codigo_postal") or "").strip(),
            "assigned_engineer_id": nested_int(payload.get("orden_instalador"), "idIngeniero"),
            "assigned_engineer_name": nested_name(nested_dict(payload.get("orden_instalador"), "ingeniero")),
            "assigned_coordinator_id": nested_int(payload.get("orden_instalador"), "idCoordinador"),
            "assigned_coordinator_name": nested_name(nested_dict(payload.get("orden_instalador"), "coordinador")),
            "has_charger": bool(to_int(payload.get("has_charger"))),
            "has_vehicle": bool(to_int(payload.get("has_vehicle"))),
            "raw_payload": payload,
            "source_created_at": parse_dt(payload.get("created_at")),
            "source_updated_at": parse_dt(payload.get("updated_at")),
            "source_last_contact_at": last_contact,
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
            placeholder_id = placeholder_remote_id(order_number=order_number)
            (
                EvergoOrder.objects.filter(remote_id=placeholder_id)
                .exclude(pk=order.pk)
                .delete()
            )

        order.sync_dynamic_field_values(payload)
        return created, order
