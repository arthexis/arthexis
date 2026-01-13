from __future__ import annotations

from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from apps.docs import rendering
from .forms import RegistrationForm, TermAcceptanceForm
from .models import RegistrationSubmission, Term


def _get_client_ip(request: HttpRequest) -> str:
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        for value in forwarded_for.split(","):
            candidate = value.strip()
            if candidate:
                return candidate
    return request.META.get("REMOTE_ADDR", "")


def _render_term(request: HttpRequest, term: Term, form: TermAcceptanceForm) -> HttpResponse:
    markdown_text = term.load_markdown()
    html, toc_html = rendering.render_markdown_with_toc(markdown_text)
    context = {
        "term": term,
        "content": html,
        "toc": toc_html,
        "form": form,
        "qr_image_url": term.reference.image_url if term.reference_id else "",
    }
    return render(request, "terms/term_detail.html", context)


def _require_security_group(request: HttpRequest, term: Term) -> None:
    if term.category != Term.Category.SECURITY_GROUP:
        return
    if not request.user.is_authenticated:
        raise Http404
    if request.user.is_superuser:
        return
    if not term.security_group_id:
        raise Http404
    if not request.user.groups.filter(pk=term.security_group_id).exists():
        raise Http404


@require_http_methods(["GET", "POST"])
def term_detail(request: HttpRequest, slug: str) -> HttpResponse:
    term = get_object_or_404(Term, slug=slug)
    if term.category == Term.Category.DRAFT and not request.user.is_staff:
        raise Http404
    _require_security_group(request, term)

    if request.method == "POST":
        form = TermAcceptanceForm(term, data=request.POST, files=request.FILES)
        if form.is_valid():
            ip_address = _get_client_ip(request)
            user_agent = request.META.get("HTTP_USER_AGENT", "")
            form.save(
                user=request.user if request.user.is_authenticated else None,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            messages.success(request, _("Your acceptance has been recorded."))
            return redirect(term.get_absolute_url())
    else:
        form = TermAcceptanceForm(term)

    return _render_term(request, term, form)


@require_http_methods(["GET", "POST"])
def registration(request: HttpRequest) -> HttpResponse:
    terms = list(Term.objects.filter(category=Term.Category.REGISTRATION).order_by("pk"))
    if not terms:
        raise Http404
    form = RegistrationForm(terms, data=request.POST or None, files=request.FILES or None)
    if request.method == "POST" and form.is_valid():
        acceptance_forms = []
        for term in terms:
            acceptance_form = TermAcceptanceForm(
                term,
                data={"accept": True},
                files={"document": form.cleaned_document_for(term)}
                if term.requires_document
                else None,
            )
            if not acceptance_form.is_valid():
                form.add_error(
                    None,
                    _("Unable to record term acceptance for %(term)s.")
                    % {"term": term.title},
                )
                return render(
                    request,
                    "terms/registration.html",
                    {
                        "form": form,
                        "terms": terms,
                        "term_fields": form.term_fields,
                        "generic_terms": _(
                            "By registering, you agree to the platform terms and conditions."
                        ),
                    },
                )
            acceptance_forms.append(acceptance_form)
        with transaction.atomic():
            user = form.save_user()
            photo_media = form.save_photo()
            submission = RegistrationSubmission.objects.create(
                user=user,
                photo_media=photo_media,
            )
            ip_address = _get_client_ip(request)
            user_agent = request.META.get("HTTP_USER_AGENT", "")
            for acceptance_form in acceptance_forms:
                acceptance_form.save(
                    user=user,
                    submission=submission,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
        messages.success(
            request,
            _("Thanks for registering! Your account is pending approval."),
        )
        return redirect("terms:registration")

    context = {
        "form": form,
        "terms": terms,
        "term_fields": form.term_fields,
        "generic_terms": _(
            "By registering, you agree to the platform terms and conditions."
        ),
    }
    return render(request, "terms/registration.html", context)
