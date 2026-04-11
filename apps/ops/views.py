"""Views supporting in-progress operation banners."""

from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.core.system.upgrade import _read_auto_upgrade_mode

OPERATOR_JOURNEY_STEP_URL_NAME = "ops:operator-journey-step"

from .models import OperatorJourneyStep
from .operator_journey import complete_step_for_user, next_step_for_user
from .redirects import safe_host_redirect
from .status_surface import build_status_surface, scoped_log_excerpts

ROLE_VALIDATION_STEP_SLUG = "validate-local-node-role"
KNOWN_NODE_ROLES = ("Terminal", "Satellite", "Control", "Watchtower")
ROLE_ALIASES = {"constellation": "Watchtower"}


def _normalize_role_name(value: str) -> str:
    """Return the canonical role name when known, else the input as-is."""

    cleaned = str(value or "").strip()
    if not cleaned:
        return ""
    role_lookup = {role.lower(): role for role in KNOWN_NODE_ROLES}
    role_lookup.update(ROLE_ALIASES)
    return role_lookup.get(cleaned.lower(), cleaned)


def _build_node_role_validation_summary() -> dict[str, object]:
    """Build operator-facing local role checks and command guidance."""

    lock_role = ""
    lock_path = Path(settings.BASE_DIR) / ".locks" / "role.lck"
    if lock_path.exists():
        try:
            lock_role = _normalize_role_name(lock_path.read_text().strip())
        except OSError:
            lock_role = ""

    configured_role = _normalize_role_name(getattr(settings, "NODE_ROLE", ""))

    local_node_role = ""
    local_node_label = "Not registered"
    try:
        from apps.nodes.models import Node
    except (ImportError, LookupError):
        local_node = None
    else:
        local_node = Node.get_local()
    if local_node:
        local_node_label = str(local_node)
        role_name = getattr(getattr(local_node, "role", None), "name", "")
        local_node_role = _normalize_role_name(role_name)

    current_role = configured_role or lock_role or local_node_role
    role_mismatch = bool(local_node_role and current_role and local_node_role != current_role)

    suggested_role = current_role or local_node_role
    normalized_slug = str(suggested_role or "").strip().lower()
    commands: list[str] = ["./configure.sh --check"]
    if normalized_slug in {role.lower() for role in KNOWN_NODE_ROLES}:
        commands.extend([f"./configure.sh --{normalized_slug}", "./service-start.sh"])
    else:
        commands.extend([f"./configure.sh --{role.lower()}" for role in KNOWN_NODE_ROLES])
        commands.append("./service-start.sh")

    return {
        "configured_role": configured_role or "Unknown",
        "lock_role": lock_role or "Unknown",
        "local_node_role": local_node_role or "Unknown",
        "local_node_label": local_node_label,
        "role_mismatch": role_mismatch,
        "commands": commands,
        "interactive": _build_role_upgrade_command_builder_context(
            default_role=suggested_role,
            base_dir=Path(settings.BASE_DIR),
        ),
    }


def _build_role_upgrade_command_builder_context(*, default_role: str, base_dir: Path) -> dict[str, object]:
    """Return current and selectable configure options for role/upgrade command building."""

    upgrade_mode = _read_auto_upgrade_mode(base_dir=base_dir)
    current_channel = str(upgrade_mode.get("mode") or "manual").strip().lower()
    if current_channel not in {"manual", "stable", "regular", "latest", "mixed"}:
        current_channel = "manual"
    auto_upgrade_enabled = bool(upgrade_mode.get("enabled", False))

    valid_roles = {role.lower() for role in KNOWN_NODE_ROLES}
    normalized_default_role = str(default_role or "").strip().lower()
    if normalized_default_role not in valid_roles:
        normalized_default_role = "terminal"

    return {
        "current": {
            "role": normalized_default_role,
            "auto_upgrade_enabled": auto_upgrade_enabled,
            "channel": current_channel,
        },
        "roles": [
            {"slug": role.lower(), "label": role}
            for role in KNOWN_NODE_ROLES
        ],
        "auto_upgrade_options": [
            {"slug": "fixed", "label": "Manual (fixed)", "enabled": False, "channel": "manual"},
            {"slug": "stable", "label": "Auto-upgrade stable", "enabled": True, "channel": "stable"},
            {"slug": "regular", "label": "Auto-upgrade regular", "enabled": True, "channel": "regular"},
            {"slug": "latest", "label": "Auto-upgrade latest", "enabled": True, "channel": "latest"},
        ],
    }


@staff_member_required
def clear_active_operation(request: HttpRequest):
    """Clear the active operation from session storage."""

    request.session.pop("ops_active_operation_id", None)
    next_url = request.GET.get("next") or ""
    return safe_host_redirect(request, next_url)


@login_required
def status_surface(request: HttpRequest) -> JsonResponse:
    """Return role-aware operational status, events, and redacted log excerpts."""

    return JsonResponse(build_status_surface(user=request.user), safe=False)


@login_required
def status_log_excerpts(request: HttpRequest) -> JsonResponse:
    """Return only scoped log excerpts for clients polling the status surface."""

    return JsonResponse({"log_excerpts": scoped_log_excerpts(user=request.user)})


@staff_member_required
def operator_journey_step(request: HttpRequest, step_id: int):
    """Render the next required journey step with embedded action frame."""

    step = (
        OperatorJourneyStep.objects.filter(pk=step_id, is_active=True, journey__is_active=True)
        .select_related("journey")
        .first()
    )
    if step is None:
        raise Http404("Journey step not found")

    next_step = next_step_for_user(user=request.user)
    if next_step is None:
        return render(request, "admin/ops/operator_journey_complete.html")
    if next_step.pk != step.pk:
        messages.warning(
            request,
            "Please complete your current required operator step before opening later items.",
        )
        return redirect(reverse(OPERATOR_JOURNEY_STEP_URL_NAME, args=[next_step.pk]))

    context = {"step": step}
    if step.slug == ROLE_VALIDATION_STEP_SLUG:
        context["node_role_validation"] = _build_node_role_validation_summary()

    return render(request, "admin/ops/operator_journey_step.html", context)


@staff_member_required
def complete_operator_journey_step(request: HttpRequest, step_id: int):
    """Complete the current required journey step and route to the next one."""

    if request.method != "POST":
        return redirect(reverse(OPERATOR_JOURNEY_STEP_URL_NAME, args=[step_id]))

    step = (
        OperatorJourneyStep.objects.filter(pk=step_id, is_active=True, journey__is_active=True)
        .select_related("journey")
        .first()
    )
    if step is None:
        raise Http404("Journey step not found")

    if not complete_step_for_user(user=request.user, step=step):
        next_step = next_step_for_user(user=request.user)
        if next_step is None:
            return redirect(reverse("admin:index"))
        messages.warning(
            request,
            "That step is not available yet. Finish the current required operator step first.",
        )
        return redirect(reverse(OPERATOR_JOURNEY_STEP_URL_NAME, args=[next_step.pk]))

    next_step = next_step_for_user(user=request.user)
    if next_step is None:
        return render(request, "admin/ops/operator_journey_complete.html")
    return redirect(reverse(OPERATOR_JOURNEY_STEP_URL_NAME, args=[next_step.pk]))
