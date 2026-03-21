from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, TemplateView

from apps.nodes.models import Node
from apps.rates.mixins import RateLimitedViewMixin
from apps.sites.utils import landing

from .forms import ChargerVendorSubmissionForm, MaintenanceRequestForm


@login_required(login_url="pages:login")
@landing("Maintenance Request")
def maintenance_request(request):
    """Allow authenticated users to schedule manual maintenance tasks."""

    form = MaintenanceRequestForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        task = form.save(commit=False)
        task.requestor = request.user if request.user.is_authenticated else None
        task.assigned_user = request.user if request.user.is_authenticated else None
        task.node = task.node or Node.get_local()
        task.is_user_data = True
        task.save()
        form.save_m2m()
        messages.success(
            request,
            _("Maintenance request scheduled for %(location)s.")
            % {"location": task.location or _("the selected location")},
        )
        return redirect("tasks:maintenance-request")

    return render(request, "tasks/maintenance_request.html", {"form": form})


class ChargerVendorSubmissionView(RateLimitedViewMixin, FormView):
    """Render a public intake form for charger vendors seeking Arthexis integration."""

    template_name = "tasks/charger_vendor_submission.html"
    form_class = ChargerVendorSubmissionForm
    success_url = reverse_lazy("tasks:charger-vendor-submission-thanks")
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
                "Thanks for sharing your charger portfolio. Our team will review the submission and follow up about the integration fit."
            ),
        )
        return super().form_valid(form)


class ChargerVendorSubmissionThanksView(TemplateView):
    """Show a lightweight confirmation page after a vendor submission."""

    template_name = "tasks/charger_vendor_submission_thanks.html"
