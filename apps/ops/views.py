"""Views supporting in-progress operation banners."""

from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

OPERATOR_JOURNEY_STEP_URL_NAME = "ops:operator-journey-step"

from .forms import OperatorJourneyProvisionSuperuserForm
from .models import OperatorJourneyStep
from .operator_journey import complete_step_for_user, next_step_for_user
from .redirects import safe_host_redirect
from .status_surface import build_status_surface, scoped_log_excerpts

ROLE_VALIDATION_STEP_SLUG = "validate-local-node-role"
PROVISION_SUPERUSER_STEP_SLUG = "provision-ops-superuser"
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
    role_mismatch = bool(
        local_node_role and current_role and local_node_role != current_role
    )

    suggested_role = current_role or local_node_role
    normalized_slug = str(suggested_role or "").strip().lower()
    commands: list[str] = ["./configure.sh --check"]
    if normalized_slug in {role.lower() for role in KNOWN_NODE_ROLES}:
        commands.extend([f"./configure.sh --{normalized_slug}", "./service-start.sh"])
    else:
        commands.extend(
            [f"./configure.sh --{role.lower()}" for role in KNOWN_NODE_ROLES]
        )
        commands.append("./service-start.sh")

    return {
        "configured_role": configured_role or "Unknown",
        "lock_role": lock_role or "Unknown",
        "local_node_role": local_node_role or "Unknown",
        "local_node_label": local_node_label,
        "role_mismatch": role_mismatch,
        "commands": commands,
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
        OperatorJourneyStep.objects.filter(
            pk=step_id, is_active=True, journey__is_active=True
        )
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
    if step.slug == PROVISION_SUPERUSER_STEP_SLUG:
        context["provision_superuser_form"] = OperatorJourneyProvisionSuperuserForm()

    return render(request, "admin/ops/operator_journey_step.html", context)


@staff_member_required
def complete_operator_journey_step(request: HttpRequest, step_id: int):
    """Complete the current required journey step and route to the next one."""

    if request.method != "POST":
        return redirect(reverse(OPERATOR_JOURNEY_STEP_URL_NAME, args=[step_id]))

    step = (
        OperatorJourneyStep.objects.filter(
            pk=step_id, is_active=True, journey__is_active=True
        )
        .select_related("journey")
        .first()
    )
    if step is None:
        raise Http404("Journey step not found")

    current_step = next_step_for_user(user=request.user)
    if current_step is None:
        return redirect(reverse("admin:index"))
    if current_step.pk != step.pk:
        messages.warning(
            request,
            "That step is not available yet. Finish the current required operator step first.",
        )
        return redirect(reverse(OPERATOR_JOURNEY_STEP_URL_NAME, args=[current_step.pk]))

    if step.slug == PROVISION_SUPERUSER_STEP_SLUG:
        provision_form = OperatorJourneyProvisionSuperuserForm(request.POST)
        if not provision_form.is_valid():
            return render(
                request,
                "admin/ops/operator_journey_step.html",
                {"step": step, "provision_superuser_form": provision_form},
            )
        new_user, password = provision_form.save()
        messages.success(
            request,
            (
                f"Created superuser {new_user.get_username()} with password: {password}. "
                "Record this securely because it will not be shown again."
            ),
        )

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
