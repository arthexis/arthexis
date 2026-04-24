"""Views supporting in-progress operation banners."""

import secrets
from pathlib import Path
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.contrib import admin
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.repos.services import github as github_service

from .forms import (
    OperatorJourneyGitHubAccessForm,
    OperatorJourneyProvisionSuperuserForm,
)
from .models import OperatorJourneyStep, OperatorJourneyStepCompletion
from .operator_journey import (
    _active_security_groups_for_user,
    complete_step_for_user,
    next_step_for_user,
    operator_journey_step_complete_url,
    operator_journey_step_url,
)
from .redirects import safe_host_redirect
from .status_surface import build_status_surface, scoped_log_excerpts

OPERATOR_JOURNEY_STEP_URL_NAME = "ops:operator-journey-step"
ADMIN_INDEX_URL_NAME = "admin:index"

ROLE_VALIDATION_STEP_SLUG = "validate-local-node-role"
PROVISION_SUPERUSER_STEP_SLUG = "provision-ops-superuser"
SETUP_GITHUB_TOKEN_STEP_SLUG = "setup-github-token"
KNOWN_NODE_ROLES = ("Terminal", "Satellite", "Control", "Watchtower")
ROLE_ALIASES = {"constellation": "Watchtower"}
GITHUB_OAUTH_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_OAUTH_SESSION_STATE_KEY = "ops_github_oauth_state"
GITHUB_OAUTH_TIMEOUT_SECONDS = 10


def _normalize_role_name(value: str) -> str:
    """Return the canonical role name when known, else the input as-is."""

    cleaned = str(value or "").strip()
    if not cleaned:
        return ""
    role_lookup = {role.lower(): role for role in KNOWN_NODE_ROLES}
    role_lookup.update(ROLE_ALIASES)
    return role_lookup.get(cleaned.lower(), cleaned)


def _build_admin_context(request: HttpRequest) -> dict[str, object]:
    """Return shared context needed by admin base templates."""

    return admin.site.each_context(request)


def _github_oauth_is_configured() -> bool:
    return bool(
        str(getattr(settings, "GITHUB_OAUTH_CLIENT_ID", "")).strip()
        and str(getattr(settings, "GITHUB_OAUTH_CLIENT_SECRET", "")).strip()
    )


def _github_oauth_scopes() -> str:
    configured_scopes = str(getattr(settings, "GITHUB_OAUTH_SCOPES", "")).strip()
    return configured_scopes or "repo read:user"


def _github_oauth_callback_url(
    request: HttpRequest, *, journey_slug: str, step_slug: str
) -> str:
    return request.build_absolute_uri(
        reverse(
            "ops:operator-journey-github-callback",
            kwargs={"journey_slug": journey_slug, "step_slug": step_slug},
        )
    )


def _can_manage_github_token(request: HttpRequest, token=None) -> bool:
    """Return whether the current user can create/update GitHubToken records."""

    try:
        from apps.repos.models import GitHubToken
    except (ImportError, LookupError):
        return False

    token_admin = admin.site._registry.get(GitHubToken)
    if token_admin is not None:
        if token is None:
            return bool(token_admin.has_add_permission(request))
        return bool(token_admin.has_change_permission(request, obj=token))

    if token is None:
        return request.user.has_perm("repos.add_githubtoken")
    return request.user.has_perm("repos.change_githubtoken")


def _build_security_group_rows(
    provision_superuser_form: OperatorJourneyProvisionSuperuserForm,
) -> list[dict[str, object]]:
    """Return normalized rows for the security-group selection table."""

    selected_group_ids: set[str] = set()
    if provision_superuser_form.is_bound:
        data = provision_superuser_form.data or {}
        if hasattr(data, "getlist"):
            raw_values = data.getlist("security_groups")
        else:
            value = data.get("security_groups", [])
            raw_values = value if isinstance(value, list | tuple | set) else [value]
        selected_group_ids = {str(value) for value in raw_values if value}
    else:
        initial = provision_superuser_form.fields["security_groups"].initial or []
        if hasattr(initial, "values_list"):
            selected_group_ids = {
                str(pk) for pk in initial.values_list("pk", flat=True)
            }
        else:
            selected_group_ids = {
                str(value.pk if hasattr(value, "pk") else value) for value in initial
            }

    rows: list[dict[str, object]] = []
    for group in provision_superuser_form.fields[
        "security_groups"
    ].queryset.prefetch_related("permissions__content_type"):
        app_labels = {
            permission.content_type.app_label
            for permission in group.permissions.all()
            if permission.content_type.app_label
        }
        if group.app:
            app_labels.add(group.app)
        app_names = ", ".join(sorted(app_labels)) if app_labels else "—"
        rows.append(
            {
                "id": group.pk,
                "apps": app_names,
                "is_staff_group": group.is_canonical_staff_group,
                "name": group.name,
                "name_label": group.name or _("Unnamed security group"),
                "selected": str(group.pk) in selected_group_ids,
            }
        )
    return rows


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
    available_roles = list(KNOWN_NODE_ROLES)
    try:
        from apps.nodes.models import UpgradePolicy
    except (ImportError, LookupError):
        upgrade_policy_options = [
            {"label": "Stable", "flags": ["--stable"]},
            {"label": "Unstable", "flags": ["--latest"]},
            {"label": "Force refresh", "flags": ["--force-refresh"]},
            {"label": "Pre-check", "flags": ["--pre-check"]},
            {"label": "No restart", "flags": ["--no-restart"]},
        ]
    else:
        upgrade_policy_options = []
        for policy in UpgradePolicy.objects.order_by("name"):
            channel_flag = (
                "--latest"
                if policy.channel == UpgradePolicy.Channel.UNSTABLE
                else "--stable"
            )
            option_label = f"Policy: {policy.name}"
            if not policy.is_active:
                option_label = f"{option_label} (inactive)"
            option_flags = [channel_flag]
            if policy.requires_pypi_packages:
                option_flags.append("--force-refresh")
            upgrade_policy_options.append(
                {"label": option_label, "flags": option_flags}
            )
        if not upgrade_policy_options:
            upgrade_policy_options = [
                {"label": "Stable", "flags": ["--stable"]},
                {"label": "Unstable", "flags": ["--latest"]},
                {"label": "Force refresh", "flags": ["--force-refresh"]},
                {"label": "Pre-check", "flags": ["--pre-check"]},
                {"label": "No restart", "flags": ["--no-restart"]},
            ]

    commands: list[str] = ["./configure.sh --check"]
    valid_roles = {_normalize_role_name(role).lower() for role in available_roles}
    if normalized_slug in valid_roles:
        commands.extend([f"./configure.sh --{normalized_slug}", "./service-start.sh"])
    else:
        commands.extend(
            [f"./configure.sh --{role.lower()}" for role in available_roles]
        )
        commands.append("./service-start.sh")

    return {
        "available_roles": available_roles,
        "configured_role": configured_role or "Unknown",
        "default_upgrade_command": "./upgrade.sh --stable --pre-check",
        "upgrade_policy_options": upgrade_policy_options,
        "lock_role": lock_role or "Unknown",
        "local_node_role": local_node_role or "Unknown",
        "local_node_label": local_node_label,
        "role_mismatch": role_mismatch,
        "commands": commands,
    }


def _resolve_oauth_step_or_redirect(
    request: HttpRequest, *, journey_slug: str, step_slug: str
) -> tuple[OperatorJourneyStep | None, HttpResponseRedirect | None]:
    """Return the current setup-github-token step or a redirect response."""

    step = (
        OperatorJourneyStep.objects.filter(
            journey__slug=journey_slug,
            slug=step_slug,
            is_active=True,
            journey__is_active=True,
        )
        .select_related("journey")
        .first()
    )
    if step is None or step.slug != SETUP_GITHUB_TOKEN_STEP_SLUG:
        raise Http404("Journey step not found")

    current_step = next_step_for_user(user=request.user)
    if current_step is None:
        return None, redirect(reverse(ADMIN_INDEX_URL_NAME))
    if current_step.pk != step.pk:
        messages.warning(
            request,
            "That step is not available yet. Finish the current required operator step first.",
        )
        return None, redirect(operator_journey_step_url(step=current_step))
    return step, None


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
def operator_journey_step(
    request: HttpRequest, journey_slug: str, step_slug: str
) -> HttpResponse | HttpResponseRedirect:
    """Render the next required journey step with guided manual workflow."""

    step = (
        OperatorJourneyStep.objects.filter(
            journey__slug=journey_slug,
            slug=step_slug,
            is_active=True,
            journey__is_active=True,
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
        return redirect(operator_journey_step_url(step=next_step))

    context = {"step": step, **_build_admin_context(request)}
    if step.slug == ROLE_VALIDATION_STEP_SLUG:
        context["node_role_validation"] = _build_node_role_validation_summary()
    if step.slug == PROVISION_SUPERUSER_STEP_SLUG:
        if not request.user.is_superuser:
            raise PermissionDenied
        provision_form = OperatorJourneyProvisionSuperuserForm()
        context["provision_superuser_form"] = provision_form
        context["security_group_rows"] = _build_security_group_rows(provision_form)
    if step.slug == SETUP_GITHUB_TOKEN_STEP_SLUG:
        github_access_form = OperatorJourneyGitHubAccessForm(user=request.user)
        github_oauth_enabled = _github_oauth_is_configured()
        context["github_access_form"] = github_access_form
        context["github_oauth_enabled"] = github_oauth_enabled
        context["github_login_url"] = reverse(
            "ops:operator-journey-github-login",
            kwargs={"journey_slug": step.journey.slug, "step_slug": step.slug},
        )
        context["github_connected_username"] = (
            github_access_form.existing_token.label
            if github_access_form.existing_token
            else ""
        )

    return render(request, "admin/ops/operator_journey_step.html", context)


@staff_member_required
def operator_journey_dashboard(request: HttpRequest) -> HttpResponse:
    """Render completed and current steps while hiding future journey steps."""

    next_step = next_step_for_user(user=request.user)
    active_groups = _active_security_groups_for_user(request.user)
    steps = list(
        OperatorJourneyStep.objects.filter(
            is_active=True,
            journey__is_active=True,
            journey__security_group__in=active_groups,
        )
        .select_related("journey")
        .order_by("journey__priority", "journey__name", "order", "id")
    )
    completion_by_step_id = {
        completion.step_id: completion
        for completion in OperatorJourneyStepCompletion.objects.filter(
            user=request.user,
            step__in=steps,
        )
    }

    visible_steps = [
        step
        for step in steps
        if step.id in completion_by_step_id
        or (next_step is not None and step.id == next_step.id)
    ]
    step_rows = []
    for step in visible_steps:
        completion = completion_by_step_id.get(step.id)
        is_current = next_step is not None and step.id == next_step.id
        step_rows.append(
            {
                "step": step,
                "is_completed": completion is not None,
                "is_current": is_current,
                "completed_at": getattr(completion, "completed_at", None),
                "url": operator_journey_step_url(step=step) if is_current else None,
            }
        )

    context = {
        **_build_admin_context(request),
        "next_step": next_step,
        "step_rows": step_rows,
    }
    return render(request, "admin/ops/operator_journey_dashboard.html", context)


@staff_member_required
def complete_operator_journey_step(
    request: HttpRequest, journey_slug: str, step_slug: str
) -> HttpResponse | HttpResponseRedirect:
    """Complete the current required journey step and route to the next one."""

    if request.method != "POST":
        return redirect(
            reverse(
                OPERATOR_JOURNEY_STEP_URL_NAME,
                kwargs={"journey_slug": journey_slug, "step_slug": step_slug},
            )
        )

    step = (
        OperatorJourneyStep.objects.filter(
            journey__slug=journey_slug,
            slug=step_slug,
            is_active=True,
            journey__is_active=True,
        )
        .select_related("journey")
        .first()
    )
    if step is None:
        raise Http404("Journey step not found")

    current_step = next_step_for_user(user=request.user)
    if current_step is None:
        return redirect(reverse(ADMIN_INDEX_URL_NAME))
    if current_step.pk != step.pk:
        messages.warning(
            request,
            "That step is not available yet. Finish the current required operator step first.",
        )
        return redirect(operator_journey_step_url(step=current_step))

    if step.slug == PROVISION_SUPERUSER_STEP_SLUG:
        if not request.user.is_superuser:
            raise PermissionDenied
        action = (request.POST.get("journey_action") or "").strip().lower()
        if action == "skip":
            with transaction.atomic():
                request.user.__class__._default_manager.select_for_update().get(
                    pk=request.user.pk
                )
                locked_step = next_step_for_user(user=request.user)
                if locked_step is None:
                    return redirect(reverse(ADMIN_INDEX_URL_NAME))
                if locked_step.pk != step.pk:
                    messages.warning(
                        request,
                        "That step is not available yet. Finish the current required operator step first.",
                    )
                    return redirect(operator_journey_step_url(step=locked_step))
                if not complete_step_for_user(user=request.user, step=step):
                    messages.warning(
                        request,
                        "That step is not available yet. Finish the current required operator step first.",
                    )
                    return redirect(operator_journey_step_url(step=locked_step))
            next_step = next_step_for_user(user=request.user)
            if next_step is None:
                return render(request, "admin/ops/operator_journey_complete.html")
            return redirect(operator_journey_step_url(step=next_step))
        provision_form = OperatorJourneyProvisionSuperuserForm(request.POST)
        if not provision_form.is_valid():
            context = {
                **_build_admin_context(request),
                "step": step,
                "provision_superuser_form": provision_form,
                "security_group_rows": _build_security_group_rows(provision_form),
            }
            return render(
                request,
                "admin/ops/operator_journey_step.html",
                context,
            )
        with transaction.atomic():
            request.user.__class__._default_manager.select_for_update().get(
                pk=request.user.pk
            )
            locked_step = next_step_for_user(user=request.user)
            if locked_step is None:
                return redirect(reverse(ADMIN_INDEX_URL_NAME))
            if locked_step.pk != step.pk:
                messages.warning(
                    request,
                    "That step is not available yet. Finish the current required operator step first.",
                )
                return redirect(operator_journey_step_url(step=locked_step))
            if not complete_step_for_user(user=request.user, step=step):
                messages.warning(
                    request,
                    "That step is not available yet. Finish the current required operator step first.",
                )
                return redirect(operator_journey_step_url(step=locked_step))
            new_user, password, created_user = provision_form.save()
        next_step = next_step_for_user(user=request.user)
        return render(
            request,
            "admin/ops/operator_journey_provision_success.html",
            {
                "created_user": created_user,
                "new_user": new_user,
                "one_time_password": password,
                "next_step": next_step,
            },
        )

    if step.slug == SETUP_GITHUB_TOKEN_STEP_SLUG:
        github_access_form = OperatorJourneyGitHubAccessForm(
            request.POST,
            user=request.user,
        )
        action = (request.POST.get("journey_action") or "").strip().lower()
        if action == "complete":
            is_valid_connection, validation_message, github_login = (
                github_access_form.validate_connection()
            )
            if not is_valid_connection:
                github_access_form.add_error(None, validation_message)
            elif (
                github_access_form.existing_token is not None
                and _can_manage_github_token(
                    request, token=github_access_form.existing_token
                )
            ):
                github_access_form.save(
                    token=github_access_form.stored_token_raw_value(),
                    username=github_login,
                )
        if action != "complete" or github_access_form.errors:
            context = {
                **_build_admin_context(request),
                "step": step,
                "github_access_form": github_access_form,
                "github_connected_username": (
                    github_access_form.existing_token.label
                    if github_access_form.existing_token
                    else ""
                ),
                "github_login_url": reverse(
                    "ops:operator-journey-github-login",
                    kwargs={"journey_slug": step.journey.slug, "step_slug": step.slug},
                ),
                "github_oauth_enabled": _github_oauth_is_configured(),
            }
            return render(
                request,
                "admin/ops/operator_journey_step.html",
                context,
            )

    if not complete_step_for_user(user=request.user, step=step):
        next_step = next_step_for_user(user=request.user)
        if next_step is None:
            return redirect(reverse(ADMIN_INDEX_URL_NAME))
        messages.warning(
            request,
            "That step is not available yet. Finish the current required operator step first.",
        )
        return redirect(operator_journey_step_url(step=next_step))

    next_step = next_step_for_user(user=request.user)
    if next_step is None:
        return render(request, "admin/ops/operator_journey_complete.html")
    return redirect(operator_journey_step_url(step=next_step))


@staff_member_required
def operator_journey_step_legacy(
    request: HttpRequest, step_id: int
) -> HttpResponseRedirect:
    """Redirect legacy numeric step URLs to slug-based canonical URLs."""

    step = (
        OperatorJourneyStep.objects.filter(
            pk=step_id,
            is_active=True,
            journey__is_active=True,
        )
        .select_related("journey")
        .first()
    )
    if step is None:
        raise Http404("Journey step not found")
    return redirect(operator_journey_step_url(step=step))


@staff_member_required
def complete_operator_journey_step_legacy(
    request: HttpRequest, step_id: int
) -> HttpResponseRedirect:
    """Redirect legacy numeric completion URLs to slug-based canonical URLs."""

    if request.method != "POST":
        canonical_step = (
            OperatorJourneyStep.objects.filter(
                journey__slug=str(step_id),
                slug="complete",
                is_active=True,
                journey__is_active=True,
            )
            .select_related("journey")
            .first()
        )
        if canonical_step is not None:
            return operator_journey_step(
                request,
                journey_slug=canonical_step.journey.slug,
                step_slug=canonical_step.slug,
            )

    step = (
        OperatorJourneyStep.objects.filter(
            pk=step_id,
            is_active=True,
            journey__is_active=True,
        )
        .select_related("journey")
        .first()
    )
    if step is None:
        raise Http404("Journey step not found")
    if request.method != "POST":
        return redirect(operator_journey_step_url(step=step))
    response = redirect(
        operator_journey_step_complete_url(step=step),
        preserve_request=True,
    )
    response.status_code = 307
    return response


@staff_member_required
def operator_journey_github_login(
    request: HttpRequest, journey_slug: str, step_slug: str
) -> HttpResponseRedirect:
    """Start GitHub OAuth login for the operator journey GitHub setup step."""

    step, blocked_response = _resolve_oauth_step_or_redirect(
        request, journey_slug=journey_slug, step_slug=step_slug
    )
    if blocked_response is not None:
        return blocked_response

    if not _github_oauth_is_configured():
        messages.error(request, "GitHub OAuth is not configured.")
        return redirect(
            reverse(
                OPERATOR_JOURNEY_STEP_URL_NAME,
                kwargs={"journey_slug": journey_slug, "step_slug": step_slug},
            )
        )

    state = secrets.token_urlsafe(32)
    request.session[GITHUB_OAUTH_SESSION_STATE_KEY] = {
        "journey_slug": step.journey.slug,
        "state": state,
        "step_slug": step.slug,
    }
    params = {
        "client_id": str(getattr(settings, "GITHUB_OAUTH_CLIENT_ID", "")).strip(),
        "redirect_uri": _github_oauth_callback_url(
            request, journey_slug=journey_slug, step_slug=step_slug
        ),
        "scope": _github_oauth_scopes(),
        "state": state,
    }
    return redirect(f"{GITHUB_OAUTH_AUTHORIZE_URL}?{urlencode(params)}")


@staff_member_required
def operator_journey_github_callback(
    request: HttpRequest, journey_slug: str, step_slug: str
) -> HttpResponseRedirect:
    """Complete GitHub OAuth login and store the token for this operator user."""

    step, blocked_response = _resolve_oauth_step_or_redirect(
        request, journey_slug=journey_slug, step_slug=step_slug
    )
    if blocked_response is not None:
        return blocked_response

    step_url = reverse(
        OPERATOR_JOURNEY_STEP_URL_NAME,
        kwargs={"journey_slug": step.journey.slug, "step_slug": step.slug},
    )
    state_payload = request.session.get(GITHUB_OAUTH_SESSION_STATE_KEY)
    request_state = (request.GET.get("state") or "").strip()
    if (
        not isinstance(state_payload, dict)
        or state_payload.get("journey_slug") != journey_slug
        or state_payload.get("step_slug") != step_slug
        or not request_state
        or request_state != state_payload.get("state")
    ):
        messages.error(
            request, "GitHub authorization could not be validated. Please try again."
        )
        return redirect(step_url)

    oauth_error = (request.GET.get("error") or "").strip()
    if oauth_error:
        messages.error(request, f"GitHub returned an error: {oauth_error}")
        return redirect(step_url)

    code = (request.GET.get("code") or "").strip()
    if not code:
        messages.error(request, "GitHub did not return an authorization code.")
        return redirect(step_url)

    form = OperatorJourneyGitHubAccessForm(user=request.user)
    if not _can_manage_github_token(request, token=form.existing_token):
        messages.error(request, "You do not have permission to save a GitHub token.")
        return redirect(step_url)

    try:
        response = requests.post(
            GITHUB_OAUTH_TOKEN_URL,
            data={
                "client_id": str(
                    getattr(settings, "GITHUB_OAUTH_CLIENT_ID", "")
                ).strip(),
                "client_secret": str(
                    getattr(settings, "GITHUB_OAUTH_CLIENT_SECRET", "")
                ).strip(),
                "code": code,
                "redirect_uri": _github_oauth_callback_url(
                    request, journey_slug=journey_slug, step_slug=step_slug
                ),
            },
            headers={"Accept": "application/json"},
            timeout=GITHUB_OAUTH_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        messages.error(request, f"GitHub authentication failed: {exc}")
        return redirect(step_url)
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    access_token = (
        str(payload.get("access_token", "")).strip()
        if isinstance(payload, dict)
        else ""
    )
    if not access_token:
        oauth_error_message = (
            str(
                payload.get("error_description")
                or payload.get("error")
                or "unknown_error"
            )
            if isinstance(payload, dict)
            else "unknown_error"
        )
        messages.error(request, f"GitHub authentication failed: {oauth_error_message}")
        return redirect(step_url)
    request.session.pop(GITHUB_OAUTH_SESSION_STATE_KEY, None)

    validation_success, validation_message, validated_login = (
        github_service.validate_token(access_token)
    )
    if not validation_success:
        messages.error(request, validation_message)
        return redirect(step_url)
    form.save(
        token=access_token,
        username=validated_login,
    )
    messages.success(
        request,
        validation_message or "Connected to GitHub.",
    )
    return redirect(step_url)
