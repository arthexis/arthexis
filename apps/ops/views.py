"""Views supporting in-progress operation banners."""

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

OPERATOR_JOURNEY_STEP_URL_NAME = "ops:operator-journey-step"

from .models import OperatorJourneyStep
from .operator_journey import complete_step_for_user, next_step_for_user
from .redirects import safe_host_redirect
from .status_surface import build_status_surface, scoped_log_excerpts


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

    return render(request, "admin/ops/operator_journey_step.html", {"step": step})


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
