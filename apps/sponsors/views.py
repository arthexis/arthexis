"""Views for sponsor registration."""

from __future__ import annotations

from django.contrib import messages
from django.urls import reverse_lazy
from django.views.generic import FormView, TemplateView

from .forms import SponsorRegistrationForm
from .services import register_sponsor


class SponsorRegistrationView(FormView):
    template_name = "sponsors/register.html"
    form_class = SponsorRegistrationForm
    success_url = reverse_lazy("sponsors:register-thank-you")

    def form_valid(self, form):
        cleaned = form.cleaned_data
        register_sponsor(
            username=cleaned["username"],
            email=cleaned["email"],
            password=cleaned["password"],
            tier=cleaned["tier"],
            renewal_mode=cleaned["renewal_mode"],
            payment_processor=cleaned["payment_processor_instance"],
            payment_reference=cleaned.get("payment_reference", ""),
        )
        messages.success(
            self.request,
            "Thanks for becoming a sponsor! Your membership is now active.",
        )
        return super().form_valid(form)


class SponsorRegistrationThanksView(TemplateView):
    template_name = "sponsors/thanks.html"
