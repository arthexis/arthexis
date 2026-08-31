from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, TemplateView

from apps.ocpp.forms import ChargerVendorSubmissionForm
from apps.ocpp.models import ChargerVendorSubmission
from utils.rate_limit_fallback import fallback_rate_limit_allows


class RateLimitedViewMixin:
    """Simple local fallback limiter for the public intake endpoint."""

    rate_limit_scope: str = "default"
    rate_limit_fallback: int | None = None
    rate_limit_window: int = 60

    def get_rate_limit_identifier(self, request: HttpRequest) -> str | None:
        return request.META.get("REMOTE_ADDR") or "unknown"

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not fallback_rate_limit_allows(
            scope_key=self.rate_limit_scope,
            identifier=self.get_rate_limit_identifier(request),
            limit=self.rate_limit_fallback,
            window=self.rate_limit_window,
        ):
            return HttpResponse(status=429)
        return super().dispatch(request, *args, **kwargs)


class ChargerVendorSubmissionView(RateLimitedViewMixin, FormView):
    """Render a public intake form for charger vendors seeking Arthexis integration."""

    template_name = "ocpp/intake/charger_vendor_submission.html"
    form_class = ChargerVendorSubmissionForm
    success_url = reverse_lazy("ocpp_intake:charger-vendor-submission-thanks")
    rate_limit_target = ChargerVendorSubmission
    rate_limit_scope = "charger-vendor-submission"
    rate_limit_fallback = 5
    rate_limit_window = 3600

    def form_valid(self, form):
        """Persist the submission and notify the user with next-step messaging."""

        submission = form.save(commit=False)
        submission.is_user_data = True
        submission.save()
        messages.success(
            self.request,
            _(
                "Thanks for sharing your charger portfolio. Our team will review the "
                "submission and follow up about the integration fit."
            ),
        )
        return super().form_valid(form)


class ChargerVendorSubmissionThanksView(TemplateView):
    """Show a lightweight confirmation page after a vendor submission."""

    template_name = "ocpp/intake/charger_vendor_submission_thanks.html"

