from django.contrib import messages
from django.db.models import Q
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView

from .forms import CVSubmissionForm
from .models import JobPosting


class PublicJobsBoardView(FormView):
    """Render open roles and process public CV submissions."""

    template_name = "jobs/public_board.html"
    form_class = CVSubmissionForm
    success_url = reverse_lazy("jobs:public-board")

    def _open_postings(self):
        now = timezone.now()
        return JobPosting.objects.filter(
            is_public=True,
            publish_at__lte=now,
        ).filter(
            Q(close_at__isnull=True) | Q(close_at__gte=now)
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["posting_queryset"] = self._open_postings()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["job_postings"] = self._open_postings()
        return context

    def form_valid(self, form):
        submission = form.save(commit=False)
        submission.is_user_data = True
        submission.save()
        messages.success(
            self.request,
            _(
                "Your CV was submitted. Our team will review your profile and contact you about next steps."
            ),
        )
        return super().form_valid(form)
