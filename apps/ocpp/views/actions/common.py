import json
from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import Http404, HttpResponse
from django.shortcuts import resolve_url
from django.utils.translation import gettext_lazy as _

from ... import store
from ...models import Charger


CALL_ACTION_LABELS = {
    "RemoteStartTransaction": _("Remote start transaction"),
    "RemoteStopTransaction": _("Remote stop transaction"),
    "RequestStartTransaction": _("Request start transaction"),
    "RequestStopTransaction": _("Request stop transaction"),
    "GetTransactionStatus": _("Get transaction status"),
    "GetDiagnostics": _("Get diagnostics"),
    "ChangeAvailability": _("Change availability"),
    "ChangeConfiguration": _("Change configuration"),
    "DataTransfer": _("Data transfer"),
    "Reset": _("Reset"),
    "TriggerMessage": _("Trigger message"),
    "ReserveNow": _("Reserve connector"),
    "CancelReservation": _("Cancel reservation"),
    "ClearCache": _("Clear cache"),
    "UnlockConnector": _("Unlock connector"),
    "UpdateFirmware": _("Update firmware"),
    "PublishFirmware": _("Publish firmware"),
    "UnpublishFirmware": _("Unpublish firmware"),
    "SetChargingProfile": _("Set charging profile"),
    "InstallCertificate": _("Install certificate"),
    "DeleteCertificate": _("Delete certificate"),
    "CertificateSigned": _("Certificate signed"),
    "GetInstalledCertificateIds": _("Get installed certificate ids"),
    "GetVariables": _("Get variables"),
    "SetVariables": _("Set variables"),
    "ClearChargingProfile": _("Clear charging profile"),
    "SetMonitoringBase": _("Set monitoring base"),
    "SetMonitoringLevel": _("Set monitoring level"),
    "SetVariableMonitoring": _("Set variable monitoring"),
    "ClearVariableMonitoring": _("Clear variable monitoring"),
    "GetMonitoringReport": _("Get monitoring report"),
    "ClearDisplayMessage": _("Clear display message"),
    "CustomerInformation": _("Customer information"),
    "GetBaseReport": _("Get base report"),
    "GetChargingProfiles": _("Get charging profiles"),
    "GetDisplayMessages": _("Get display messages"),
    "GetReport": _("Get report"),
    "SetDisplayMessage": _("Set display message"),
    "SetNetworkProfile": _("Set network profile"),
    "GetCompositeSchedule": _("Get composite schedule"),
    "GetLocalListVersion": _("Get local list version"),
    "GetLog": _("Get log"),
}

CALL_EXPECTED_STATUSES: dict[str, set[str] | None] = {
    "RemoteStartTransaction": {"Accepted"},
    "RemoteStopTransaction": {"Accepted"},
    "RequestStartTransaction": {"Accepted"},
    "RequestStopTransaction": {"Accepted"},
    "GetDiagnostics": None,
    "ChangeAvailability": {"Accepted", "Scheduled"},
    "ChangeConfiguration": {"Accepted", "Rejected", "RebootRequired"},
    "DataTransfer": {"Accepted"},
    "Reset": {"Accepted"},
    "TriggerMessage": {"Accepted"},
    "ReserveNow": {"Accepted"},
    "CancelReservation": {"Accepted", "Rejected"},
    "ClearCache": {"Accepted", "Rejected"},
    "UnlockConnector": {"Unlocked", "Accepted"},
    "UpdateFirmware": None,
    "PublishFirmware": {"Accepted", "Rejected"},
    "UnpublishFirmware": {"Accepted", "Rejected"},
    "SetChargingProfile": {"Accepted", "Rejected", "NotSupported"},
    "InstallCertificate": {"Accepted", "Rejected"},
    "DeleteCertificate": {"Accepted", "Rejected"},
    "CertificateSigned": {"Accepted", "Rejected"},
    "GetInstalledCertificateIds": {"Accepted", "NotSupported"},
    "ClearChargingProfile": {"Accepted", "Unknown", "NotSupported"},
    "SetMonitoringBase": {"Accepted", "Rejected", "NotSupported"},
    "SetMonitoringLevel": {"Accepted", "Rejected", "NotSupported"},
    "ClearVariableMonitoring": {"Accepted", "Rejected", "NotSupported"},
    "GetMonitoringReport": {"Accepted", "Rejected", "NotSupported"},
    "ClearDisplayMessage": {"Accepted", "Unknown"},
    "CustomerInformation": {"Accepted", "Rejected", "Invalid"},
    "GetBaseReport": {"Accepted", "Rejected", "NotSupported", "EmptyResultSet"},
    "GetChargingProfiles": {"Accepted", "NoProfiles"},
    "GetDisplayMessages": {"Accepted", "Unknown"},
    "GetReport": {"Accepted", "Rejected", "NotSupported", "EmptyResultSet"},
    "SetDisplayMessage": {
        "Accepted",
        "NotSupportedMessageFormat",
        "Rejected",
        "NotSupportedPriority",
        "NotSupportedState",
        "UnknownTransaction",
    },
    "SetNetworkProfile": {"Accepted", "Rejected", "Failed"},
    "GetCompositeSchedule": {"Accepted", "Rejected"},
    "GetLocalListVersion": None,
    "GetLog": {"Accepted", "Rejected"},
}


@dataclass
class ActionCall:
    msg: str
    message_id: str
    ocpp_action: str
    expected_statuses: set[str] | None = None
    log_key: str | None = None


@dataclass
class ActionContext:
    cid: str
    connector_value: int | None
    charger: Charger | None
    ws: object
    log_key: str
    request: object | None = None


def _parse_request_body(request) -> dict:
    try:
        return json.loads(request.body.decode()) if request.body else {}
    except json.JSONDecodeError:
        return {}


def _get_or_create_charger(cid: str, connector_value: int | None) -> Charger | None:
    if connector_value is None:
        charger_obj = (
            Charger.objects.filter(charger_id=cid, connector_id__isnull=True)
            .order_by("pk")
            .first()
        )
    else:
        charger_obj = (
            Charger.objects.filter(charger_id=cid, connector_id=connector_value)
            .order_by("pk")
            .first()
        )
    if charger_obj is None:
        if connector_value is None:
            charger_obj, _created = Charger.objects.get_or_create(
                charger_id=cid, connector_id=None
            )
        else:
            charger_obj, _created = Charger.objects.get_or_create(
                charger_id=cid, connector_id=connector_value
            )
    return charger_obj


def _format_details(value: object) -> str:
    """Return a JSON representation of ``value`` suitable for error messages."""

    if value in (None, ""):
        return ""
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text
        return ""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    except TypeError:
        return str(value)


def _evaluate_pending_call_result(
    message_id: str,
    ocpp_action: str,
    *,
    expected_statuses: set[str] | None = None,
) -> tuple[bool, str | None, int | None]:
    """Wait for a pending call result and translate failures into messages."""

    action_label = CALL_ACTION_LABELS.get(ocpp_action, ocpp_action)
    result = store.wait_for_pending_call(message_id, timeout=5.0)
    if result is None:
        detail = _("%(action)s did not receive a response from the charger.") % {
            "action": action_label,
        }
        return False, detail, 504
    if not result.get("success", True):
        parts: list[str] = []
        error_code = str(result.get("error_code") or "").strip()
        if error_code:
            parts.append(_("code=%(code)s") % {"code": error_code})
        error_description = str(result.get("error_description") or "").strip()
        if error_description:
            parts.append(
                _("description=%(description)s") % {"description": error_description}
            )
        error_details = result.get("error_details")
        details_text = _format_details(error_details)
        if details_text:
            parts.append(_("details=%(details)s") % {"details": details_text})
        if parts:
            detail = _("%(action)s failed: %(details)s") % {
                "action": action_label,
                "details": ", ".join(parts),
            }
        else:
            detail = _("%(action)s failed.") % {"action": action_label}
        return False, detail, 400
    payload = result.get("payload")
    payload_dict = payload if isinstance(payload, dict) else {}
    if expected_statuses is not None:
        status_value = str(payload_dict.get("status") or "").strip()
        normalized_expected = {value.casefold() for value in expected_statuses if value}
        remaining = {k: v for k, v in payload_dict.items() if k != "status"}
        if not status_value:
            detail = _("%(action)s response did not include a status.") % {
                "action": action_label,
            }
            return False, detail, 400
        if normalized_expected and status_value.casefold() not in normalized_expected:
            detail = _("%(action)s rejected with status %(status)s.") % {
                "action": action_label,
                "status": status_value,
            }
            extra = _format_details(remaining)
            if extra:
                detail += " " + _("Details: %(details)s") % {"details": extra}
            return False, detail, 400
        if status_value.casefold() == "rejected":
            detail = _("%(action)s rejected with status %(status)s.") % {
                "action": action_label,
                "status": status_value,
            }
            extra = _format_details(remaining)
            if extra:
                detail += " " + _("Details: %(details)s") % {"details": extra}
            return False, detail, 400
    return True, None, None


def _normalize_connector_slug(slug: str | None) -> tuple[int | None, str]:
    """Return connector value and normalized slug or raise 404."""

    try:
        value = Charger.connector_value_from_slug(slug)
    except ValueError as exc:  # pragma: no cover - defensive guard
        raise Http404("Invalid connector") from exc
    return value, Charger.connector_slug_from_value(value)


def _ensure_charger_access(
    user,
    charger: Charger,
    *,
    request=None,
) -> HttpResponse | None:
    """Ensure ``user`` may view ``charger``."""

    if charger.is_visible_to(user):
        return None
    if (
        request is not None
        and not getattr(user, "is_authenticated", False)
        and charger.has_owner_scope()
    ):
        return redirect_to_login(
            request.get_full_path(),
            login_url=resolve_url(settings.LOGIN_URL),
        )
    raise Http404("Charger not found")


def _build_component_variable_base(entry: dict) -> tuple[dict[str, object], str | None]:
    component = entry.get("component")
    variable = entry.get("variable")
    if component is None or variable is None:
        component_name = entry.get("componentName")
        variable_name = entry.get("variableName")
        if component_name in (None, "") or variable_name in (None, ""):
            return {}, "component and variable are required"
        component = {"name": component_name}
        variable = {"name": variable_name}
        component_instance = entry.get("componentInstance")
        if component_instance not in (None, ""):
            component["instance"] = component_instance
        variable_instance = entry.get("variableInstance")
        if variable_instance not in (None, ""):
            variable["instance"] = variable_instance
    if not isinstance(component, dict) or not isinstance(variable, dict):
        return {}, "component and variable must be objects"
    component_name = str(component.get("name") or "").strip()
    variable_name = str(variable.get("name") or "").strip()
    if not component_name or not variable_name:
        return {}, "component.name and variable.name required"
    return {"component": component, "variable": variable}, None


def _build_component_variable_payload(entry: dict) -> tuple[dict[str, object], str | None]:
    payload, error = _build_component_variable_base(entry)
    if error:
        return payload, error
    attribute_type = entry.get("attributeType")
    if attribute_type not in (None, ""):
        payload["attributeType"] = attribute_type
    return payload, None


def _build_component_variable_entry(entry: dict) -> tuple[dict[str, object], str | None]:
    return _build_component_variable_base(entry)
