import json
import logging
from collections.abc import Mapping

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.cards.command_burn import (
    DEFAULT_COMMAND_CARD_BURN_TIMEOUT,
    CommandCardBurnError,
    command_template_queryset,
    latest_scanned_command_card_source,
    resolve_command_card_burn_source,
)
from apps.cards.command_layout import provenance_key_for_reader
from apps.cards.login_poll import request_has_rfid_login_poll_token
from apps.cards.models import RFID, RFIDAttempt, RFIDCommandTemplate
from apps.cards.public_usage import build_public_rfid_usage
from apps.cards.reader import write_current_card_command
from apps.cards.sync import apply_rfid_payload, serialize_rfid
from apps.nodes.models import Node, NodeFeature
from apps.nodes.utils import ensure_feature_enabled
from apps.nodes.views import _clean_requester_hint, _load_signed_node
from apps.sites.utils import (
    landing,
    require_site_operator_or_staff,
    user_in_site_operator_group,
)

from .reader import validate_rfid_value
from .scanner import enable_deep_read_mode, poll_scan_attempt, record_scan_attempt
from .utils import build_mode_toggle

logger = logging.getLogger(__name__)

SENSITIVE_SCAN_ERROR_KEYS = {
    "exception",
    "exc_info",
    "stack",
    "stacktrace",
    "traceback",
}

COMMAND_TEMPLATE_SUBVIEWS = {
    RFIDCommandTemplate.ViewKind.COMMAND_OUTPUT: "cards/command_templates/subviews/command_output.html",
    RFIDCommandTemplate.ViewKind.FEEDBACK: "cards/command_templates/subviews/feedback.html",
    RFIDCommandTemplate.ViewKind.HEALTH: "cards/command_templates/subviews/health.html",
    RFIDCommandTemplate.ViewKind.UPGRADE: "cards/command_templates/subviews/upgrade.html",
}


def _request_wants_json(request):
    """Return True if the request expects a JSON response."""

    accept = request.headers.get("accept", "")
    if "application/json" in accept.lower():
        return True
    # Fallback for older callers that mark AJAX requests without Accept headers.
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def _feature_enabled(slug: str) -> bool:
    """Return ``True`` when the feature identified by ``slug`` is active."""

    feature = NodeFeature.objects.filter(slug=slug).first()
    if not feature:
        return False
    try:
        return bool(feature.is_enabled)
    except Exception:
        return False


def _json_display(value: object) -> str:
    return json.dumps(
        {} if value is None else value, indent=2, sort_keys=True, default=str
    )


def _public_scan_error_message(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "invalid json payload":
        return "Invalid JSON payload"
    if normalized == "invalid offset":
        return "Invalid offset"
    if normalized == "invalid rfid key":
        return "Invalid RFID key"
    if normalized == "invalid rfid value":
        return "Invalid RFID value"
    if normalized == "no rfid card detected":
        return "No RFID card detected"
    if normalized == "no scanner service available":
        return "no scanner service available"
    if normalized == "permission denied":
        return "Permission denied"
    if normalized == "rfid must be a string":
        return "RFID must be a string"
    if normalized == "rfid must be hexadecimal digits":
        return "RFID must be hexadecimal digits"
    if normalized == "rfid scanner unavailable":
        return "RFID scanner unavailable"
    if normalized == "rfid value is required":
        return "RFID value is required"
    if normalized == "scanner service unavailable":
        return "scanner service unavailable"
    if normalized == "unable to read rfid block":
        return "Unable to read RFID block"
    if normalized == "unable to write rfid block":
        return "Unable to write RFID block"
    return "RFID scanner unavailable"


def _is_sensitive_scan_key(key: object) -> bool:
    normalized_key = str(key).lower()
    sensitive_keys = {key.lower() for key in SENSITIVE_SCAN_ERROR_KEYS}
    return normalized_key in sensitive_keys or any(
        sensitive_key in normalized_key for sensitive_key in sensitive_keys
    )


def _public_scan_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _public_scan_result(value)
    if isinstance(value, list | tuple):
        return [_public_scan_value(item) for item in value]
    return value


def _public_scan_result(result: Mapping) -> dict:
    public_result = {}
    raw_error = None
    suppressed_sensitive_detail = False
    for key, value in result.items():
        normalized_key = str(key).lower()
        if normalized_key == "error":
            raw_error = value
            continue
        if _is_sensitive_scan_key(key):
            suppressed_sensitive_detail = True
            continue
        public_result[key] = _public_scan_value(value)
    if raw_error is not None:
        public_message = _public_scan_error_message(raw_error)
        if public_message == "RFID scanner unavailable":
            logger.warning("Suppressed RFID scan error detail")
        return {"error": public_message}
    if suppressed_sensitive_detail:
        logger.warning("Suppressed RFID scan diagnostic detail")
        return {"error": "RFID scanner unavailable"}
    return public_result


def _can_view_command_template_history(user) -> bool:
    return bool(
        user.is_authenticated
        and (
            user.is_staff
            or user.is_superuser
            or user.has_perm("cards.view_rfidcommandexecution")
            or user_in_site_operator_group(user)
        )
    )


def scan_next(request):
    """Return the next scanned RFID tag or validate a client-provided value."""

    node = Node.get_local()
    ensure_feature_enabled("rfid-scanner", node=node, logger=logger)
    rfid_feature_enabled = _feature_enabled("rfid-scanner")
    role_name = getattr(getattr(node, "role", None), "name", None)
    user = request.user
    wants_json = _request_wants_json(request) or request.method == "POST"
    allow_anonymous_get = (
        role_name == "Control"
        and request.method == "GET"
        and request_has_rfid_login_poll_token(request)
    )
    if not user.is_authenticated and not allow_anonymous_get:
        if wants_json:
            return JsonResponse({"error": "Authentication required"}, status=401)
        return redirect_to_login(request.get_full_path(), reverse("pages:login"))
    if not allow_anonymous_get and not (
        user.is_staff or user.is_superuser or user_in_site_operator_group(user)
    ):
        if wants_json:
            return JsonResponse({"error": "Permission denied"}, status=403)
        raise PermissionDenied
    if request.method == "POST":
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({"error": "Invalid JSON payload"}, status=400)
        rfid = payload.get("rfid") or payload.get("value")
        kind = payload.get("kind")
        endianness = payload.get("endianness")
        result = validate_rfid_value(rfid, kind=kind, endianness=endianness)
        if not result.get("error") and result.get("rfid"):
            attempt = record_scan_attempt(
                result,
                source=RFIDAttempt.Source.BROWSER,
                status=RFIDAttempt.Status.SCANNED,
            )
            if attempt:
                result["attempt_id"] = attempt.pk
    else:
        endianness = request.GET.get("endianness")
        after_id = request.GET.get("after")
        try:
            after_id_value = int(after_id) if after_id else None
        except (TypeError, ValueError):
            after_id_value = None
        result = poll_scan_attempt(
            after_id=after_id_value,
            endianness=endianness,
        )
    result = _public_scan_result(result)
    status = 500 if result.get("error") else 200
    return JsonResponse(result, status=status)


@require_GET
def public_card_usage(request, public_token: str):
    """Public cardholder-safe RFID usage page for tokenized QR links."""

    token = str(public_token or "").strip()
    tag = get_object_or_404(
        RFID.objects.filter(public_token_enabled=True)
        .exclude(public_token__isnull=True)
        .exclude(public_token=""),
        public_token=token,
    )
    context = build_public_rfid_usage(tag)
    return render(request, "cards/public_card_usage.html", context)


@require_GET
def command_template_detail(request, slug: str):
    """Public command-template view intended for card QR links."""

    template = get_object_or_404(
        RFIDCommandTemplate.objects,
        slug=slug,
    )
    can_view_history = _can_view_command_template_history(request.user)
    executions = []
    if can_view_history:
        executions = list(
            template.command_executions.select_related("rfid", "run_as_user").order_by(
                "-triggered_at", "-pk"
            )[:50]
        )
    latest_execution = executions[0] if executions else None
    card_checks = []
    if can_view_history:
        related_cards = list(
            RFID.objects.filter(
                Q(command_template=template) | Q(command_card_name=template.name)
            )
            .distinct()
            .order_by("label_id")
        )
        related_card_ids = [tag.pk for tag in related_cards]
        latest_by_card: dict[int, object] = {}
        if related_card_ids:
            for execution in (
                template.command_executions.filter(rfid_id__in=related_card_ids)
                .select_related("rfid")
                .order_by("rfid_id", "-triggered_at", "-pk")
            ):
                latest_by_card.setdefault(execution.rfid_id, execution)
        card_checks = [
            template.card_consistency(tag, latest_execution=latest_by_card.get(tag.pk))
            for tag in related_cards
        ]
    latest_result = latest_execution.result if latest_execution is not None else {}
    latest_payload = (
        latest_result.get("payload") if isinstance(latest_result, dict) else {}
    )
    if not isinstance(latest_payload, dict):
        latest_payload = {}
    qr_url = template.get_qr_target_url(request.build_absolute_uri("/"))
    context = {
        "command_template": template,
        "template_params_json": _json_display(template.command_params),
        "template_sigils_json": _json_display(template.command_sigils),
        "latest_execution": latest_execution,
        "latest_result_json": _json_display(latest_result),
        "latest_payload": latest_payload,
        "executions": executions,
        "card_checks": card_checks,
        "can_view_execution_history": can_view_history,
        "valid_card_count": sum(1 for check in card_checks if check["valid"]),
        "subview_template": COMMAND_TEMPLATE_SUBVIEWS.get(
            template.view_kind,
            "cards/command_templates/subviews/general.html",
        ),
        "qr_url": qr_url,
        "qr_data_uri": template.qr_data_uri(qr_url),
    }
    return render(request, "cards/command_template_detail.html", context)


def _command_template_burn_timeout(value: object) -> float:
    try:
        timeout = float(value or DEFAULT_COMMAND_CARD_BURN_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_COMMAND_CARD_BURN_TIMEOUT
    return max(1.0, min(timeout, 300.0))


def _source_context(source) -> dict:
    if source is None or source.source_rfid is None:
        return {}
    tag = source.source_rfid
    return {
        "source_rfid": tag,
        "source_label": tag.display_label,
        "source_template": source.template,
    }


def _public_burn_error(result: dict | None = None) -> str:
    if result and result.get("error") == "No RFID card detected":
        return "No RFID card detected"
    return "RFID operation failed"


def _public_burn_source_error() -> str:
    return "Command template unavailable"


def _public_burn_result(result: dict | None) -> dict | None:
    if result is None:
        return None
    public_keys = {
        "card_name",
        "command",
        "label_id",
        "requires_owner",
        "rfid",
        "template",
        "template_url",
    }
    public_result = {key: result[key] for key in public_keys if key in result}
    if result.get("error") or result.get("errors"):
        public_result["error"] = _public_burn_error(result)
    return public_result


def _write_command_template_to_presented_card(
    request,
    template: RFIDCommandTemplate,
) -> dict:
    provenance = provenance_key_for_reader(Node.get_local() or "")
    result = write_current_card_command(
        name=template.name,
        command=template.command_name,
        params=template.command_params,
        sigils=template.command_sigils,
        timeout=_command_template_burn_timeout(request.POST.get("timeout")),
        writer_id=request.POST.get("writer_id") or None,
        provenance_key=provenance,
        lifecycle_mode=template.lifecycle_mode,
    )
    if result.get("error") or result.get("errors"):
        return result
    tag = RFID.objects.filter(pk=result.get("label_id")).first()
    update_fields: list[str] = []
    if tag is not None and tag.command_template_id != template.pk:
        tag.command_template = template
        update_fields.append("command_template")
    if (
        tag is not None
        and template.requires_owner
        and request.user.is_authenticated
        and tag.owner_user_id != request.user.pk
    ):
        tag.owner_user = request.user
        update_fields.append("owner_user")
    if tag is not None and update_fields:
        tag.save(update_fields=update_fields)
    result["template"] = template.name
    result["template_url"] = template.get_absolute_url()
    result["requires_owner"] = template.requires_owner
    return result


@landing("RFID Command Card Burner")
@require_http_methods(["GET", "POST"])
def command_template_burn(request):
    """Operator view for burning RFID command-card templates."""

    auth_response = require_site_operator_or_staff(request)
    if auth_response is not None:
        return auth_response

    if request.method == "POST":
        selected_value = request.POST.get("template")
    else:
        selected_value = request.GET.get("template")
    templates = list(command_template_queryset())
    previous_source = latest_scanned_command_card_source()
    error = ""
    result = None
    burn_source = None
    selected_template = None

    if request.method == "POST":
        try:
            burn_source = resolve_command_card_burn_source(selected_value)
        except CommandCardBurnError:
            logger.warning("Command-card burn source resolution failed", exc_info=True)
            error = _public_burn_source_error()
        else:
            selected_template = burn_source.template
            result = _write_command_template_to_presented_card(
                request,
                selected_template,
            )
            if result.get("error") or result.get("errors"):
                error = _public_burn_error(result)
    elif selected_value:
        try:
            burn_source = resolve_command_card_burn_source(selected_value)
            selected_template = burn_source.template
        except CommandCardBurnError:
            logger.warning("Command-card burn source resolution failed", exc_info=True)
            error = _public_burn_source_error()

    display_source = burn_source or previous_source
    context = {
        "templates": templates,
        "selected_value": selected_value or "",
        "selected_template": selected_template,
        "previous_source": previous_source,
        "burn_source": display_source,
        "result": result,
        "error": error,
        "default_timeout": DEFAULT_COMMAND_CARD_BURN_TIMEOUT,
        **_source_context(display_source),
    }
    if _request_wants_json(request):
        payload = {
            "error": error,
            "result": _public_burn_result(result),
            "selected_template": selected_template.name if selected_template else "",
            "source_label": context.get("source_label", ""),
            "templates": [template.name for template in templates],
        }
        status = 400 if error else 200
        return JsonResponse(payload, status=status)
    return render(request, "cards/command_template_burn.html", context)


@csrf_exempt
def export_rfids(request):
    """Return serialized RFID records for authenticated peers."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=405)

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid json"}, status=400)

    requester = payload.get("requester")
    if not requester:
        return JsonResponse({"detail": "requester required"}, status=400)

    requester_mac = _clean_requester_hint(payload.get("requester_mac"))
    requester_public_key = _clean_requester_hint(
        payload.get("requester_public_key"), strip=False
    )
    node, error_response = _load_signed_node(
        request,
        requester,
        mac_address=requester_mac,
        public_key=requester_public_key,
    )
    if error_response is not None:
        return error_response

    tags = [serialize_rfid(tag) for tag in RFID.objects.all().order_by("label_id")]

    return JsonResponse({"rfids": tags})


@csrf_exempt
def import_rfids(request):
    """Import RFID payloads from a trusted peer."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=405)

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid json"}, status=400)

    requester = payload.get("requester")
    if not requester:
        return JsonResponse({"detail": "requester required"}, status=400)

    requester_mac = _clean_requester_hint(payload.get("requester_mac"))
    requester_public_key = _clean_requester_hint(
        payload.get("requester_public_key"), strip=False
    )
    node, error_response = _load_signed_node(
        request,
        requester,
        mac_address=requester_mac,
        public_key=requester_public_key,
    )
    if error_response is not None:
        return error_response

    rfids = payload.get("rfids", [])
    if not isinstance(rfids, list):
        return JsonResponse({"detail": "rfids must be a list"}, status=400)

    created = 0
    updated = 0
    linked_accounts = 0
    missing_accounts: list[str] = []
    errors = 0

    for entry in rfids:
        if not isinstance(entry, Mapping):
            errors += 1
            continue
        outcome = apply_rfid_payload(entry, origin_node=node)
        if not outcome.ok:
            errors += 1
            if outcome.error:
                missing_accounts.append(outcome.error)
            continue
        if outcome.created:
            created += 1
        else:
            updated += 1
        linked_accounts += outcome.accounts_linked
        missing_accounts.extend(outcome.missing_accounts)

    return JsonResponse(
        {
            "processed": len(rfids),
            "created": created,
            "updated": updated,
            "accounts_linked": linked_accounts,
            "missing_accounts": missing_accounts,
            "errors": errors,
        }
    )


@require_POST
@staff_member_required
def scan_deep(_request):
    """Enable deep read mode on the RFID scanner."""
    result = enable_deep_read_mode()
    status = 500 if result.get("error") else 200
    return JsonResponse(result, status=status)


@landing("Identity Validator")
def reader(request):
    """Public page to scan RFID tags."""
    node = Node.get_local()
    ensure_feature_enabled("rfid-scanner", node=node, logger=logger)

    auth_response = require_site_operator_or_staff(request)
    if auth_response is not None:
        return auth_response

    table_mode, toggle_url, toggle_label = build_mode_toggle(request)
    rfid_feature_enabled = _feature_enabled("rfid-scanner")

    context = {
        "scan_url": reverse("rfid-scan-next"),
        "table_mode": table_mode,
        "toggle_url": toggle_url,
        "toggle_label": toggle_label,
        "show_release_info": request.user.is_staff,
        "default_endianness": RFID.BIG_ENDIAN,
        "rfid_feature_enabled": rfid_feature_enabled,
    }
    if request.user.is_staff:
        context["admin_change_url_template"] = reverse(
            "admin:cards_rfid_change", args=[0]
        )
        context["deep_read_url"] = reverse("rfid-scan-deep")
        context["admin_view_url"] = reverse("admin:cards_rfid_scan")
    return render(request, "cards/reader.html", context)


reader.required_features_any = frozenset({"rfid-scanner"})
