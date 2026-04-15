from __future__ import annotations

import json
from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.cards.models import OfferingSoul
from apps.emails import mailer
from apps.survey.forms import SurveySubmissionForm
from apps.survey.models import Survey, SurveyAnswer, SurveyResponse

from .forms import SoulOfferingUploadForm, SoulRegistrationStartForm
from .models import Soul, SoulRegistrationSession
from .services import build_soul_package
from .services.checkout import CHECKOUT_SOUL_KEY

REG_SESSION_KEY = "soul_registration_session_id"
SOUL_SURVEY_TITLE = "Soul Seed Registration"
LEGACY_SOUL_SURVEY_TITLE = "Soul Registration"
START_REGISTRATION_WARNING = "Start a Soul Seed registration first."


@require_GET
def register_landing(request: HttpRequest) -> HttpResponse:
    context = {
        "start_form": SoulRegistrationStartForm(),
    }
    return render(request, "souls/register_landing.html", context)


@require_POST
def register_start(request: HttpRequest) -> HttpResponse:
    form = SoulRegistrationStartForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please provide a valid email address.")
        return redirect("souls:register_landing")

    email = form.cleaned_data["email"].strip().lower()
    ip = request.META.get("REMOTE_ADDR", "")
    user_agent = request.META.get("HTTP_USER_AGENT", "")
    session = SoulRegistrationSession.objects.create(
        email=email,
        ip_hash=SoulRegistrationSession.digest_value(ip),
        ua_hash=SoulRegistrationSession.digest_value(user_agent),
    )
    request.session[REG_SESSION_KEY] = session.id
    return redirect("souls:register_offering")


def _load_registration_session(request: HttpRequest) -> SoulRegistrationSession:
    registration_id = request.session.get(REG_SESSION_KEY)
    if not registration_id:
        raise ValidationError("Registration session not found.")
    return get_object_or_404(SoulRegistrationSession, pk=registration_id)


@require_http_methods(["GET", "POST"])
def register_offering(request: HttpRequest) -> HttpResponse:
    try:
        registration = _load_registration_session(request)
    except ValidationError:
        messages.warning(request, START_REGISTRATION_WARNING)
        return redirect("souls:register_landing")

    form = SoulOfferingUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        offering_file = form.cleaned_data["offering"]
        offering_soul = OfferingSoul.create_from_upload(offering_file)
        registration.offering_soul = offering_soul
        registration.state = SoulRegistrationSession.State.OFFERING_DONE
        registration.save(update_fields=["offering_soul", "state"])
        return redirect("souls:register_survey")

    return render(request, "souls/register_offering.html", {"form": form, "registration": registration})


@require_http_methods(["GET", "POST"])
def register_survey(request: HttpRequest) -> HttpResponse:
    try:
        registration = _load_registration_session(request)
    except ValidationError:
        messages.warning(request, START_REGISTRATION_WARNING)
        return redirect("souls:register_landing")

    if not registration.offering_soul_id:
        messages.warning(request, "Upload an offering before answering the survey.")
        return redirect("souls:register_offering")

    survey = Survey.objects.filter(title=SOUL_SURVEY_TITLE, is_active=True).first()
    if survey is None:
        survey = Survey.objects.filter(title=LEGACY_SOUL_SURVEY_TITLE, is_active=True).first()
    if survey is None:
        raise Http404
    participant_token = registration.participant_token or uuid4().hex
    if registration.participant_token != participant_token:
        registration.participant_token = participant_token
        registration.save(update_fields=["participant_token"])

    existing = registration.survey_response or SurveyResponse.objects.filter(
        survey=survey,
        participant_token=participant_token,
    ).first()
    if request.method == "GET":
        if existing:
            registration.survey_response = existing
            registration.state = SoulRegistrationSession.State.SURVEY_DONE
            registration.save(update_fields=["survey_response", "state"])
            _send_verification_email(request, registration)
            return redirect("souls:register_complete")
        form = SurveySubmissionForm(survey=survey)
        return render(request, "souls/register_survey.html", {"form": form, "survey": survey})

    if existing:
        messages.info(request, "Survey already submitted for this registration session.")
        return redirect("souls:register_complete")

    form = SurveySubmissionForm(request.POST, survey=survey)
    if not form.is_valid():
        return render(request, "souls/register_survey.html", {"form": form, "survey": survey})

    with transaction.atomic():
        response = SurveyResponse.objects.create(survey=survey, participant_token=participant_token)
        for question in form.questions:
            field_name = SurveySubmissionForm._field_name(question.pk)
            submitted = form.cleaned_data[field_name]
            selected_ids = submitted if isinstance(submitted, list) else [submitted]
            options = [option for option in question.options.all() if str(option.pk) in selected_ids]
            answer = SurveyAnswer.objects.create(response=response, question=question)
            answer.selected_options.set(options)

        registration.survey_response = response
        registration.state = SoulRegistrationSession.State.SURVEY_DONE
        registration.save(update_fields=["survey_response", "state"])

    _send_verification_email(request, registration)
    return redirect("souls:register_complete")


def _send_verification_email(request: HttpRequest, registration: SoulRegistrationSession) -> None:
    if registration.state == SoulRegistrationSession.State.EMAIL_SENT and registration.verification_token_hash:
        return

    token, token_hash = SoulRegistrationSession.create_verification_token()
    registration.verification_token_hash = token_hash
    registration.verification_sent_at = timezone.now()
    registration.state = SoulRegistrationSession.State.EMAIL_SENT
    registration.save(
        update_fields=[
            "verification_token_hash",
            "verification_sent_at",
            "state",
        ]
    )

    verification_url = request.build_absolute_uri(
        reverse("souls:register_verify", kwargs={"session_id": registration.id, "token": token})
    )
    mailer.send(
        subject="Verify your Soul Seed Registration",
        message=f"Verify your Soul Seed registration: {verification_url}",
        recipient_list=[registration.email],
        fail_silently=True,
    )


@require_GET
def register_complete(request: HttpRequest) -> HttpResponse:
    try:
        registration = _load_registration_session(request)
    except ValidationError:
        messages.warning(request, START_REGISTRATION_WARNING)
        return redirect("souls:register_landing")
    return render(request, "souls/register_complete.html", {"registration": registration})


def _registration_auth_backend() -> str | None:
    for backend in settings.AUTHENTICATION_BACKENDS:
        if "LocalhostAdminBackend" not in backend:
            return backend
    return settings.AUTHENTICATION_BACKENDS[0] if settings.AUTHENTICATION_BACKENDS else None


@require_GET
def register_verify(request: HttpRequest, session_id: int, token: str) -> HttpResponse:
    token_hash = SoulRegistrationSession.digest_value(token)
    registration = (
        SoulRegistrationSession.objects.select_related("offering_soul", "survey_response")
        .filter(
            id=session_id,
            state=SoulRegistrationSession.State.EMAIL_SENT,
            verification_token_hash=token_hash,
            expires_at__gte=timezone.now(),
        )
        .first()
    )
    if not registration or not registration.verify_token(token):
        messages.error(request, "Verification token is invalid or expired.")
        return redirect("souls:register_landing")

    user_model = get_user_model()
    with transaction.atomic():
        matching_users = list(
            user_model.objects.select_related("soul").filter(email__iexact=registration.email).order_by("id")[:2]
        )
        if len(matching_users) > 1:
            messages.error(request, "Registration could not be completed for this email address.")
            return redirect("souls:register_landing")

        user = matching_users[0] if matching_users else None
        if user is None:
            username = registration.email.split("@", 1)[0]
            candidate = username
            suffix = 1
            while user_model.objects.filter(username=candidate).exists() and suffix < 1000:
                suffix += 1
                candidate = f"{username}{suffix}"
            user = user_model.objects.create_user(
                username=candidate,
                email=registration.email,
                password=user_model.objects.make_random_password(),
            )

        package, soul_id, survey_digest, email_hash = build_soul_package(
            registration_session=registration,
            user=user,
        )
        existing_soul = getattr(user, "soul", None)
        if existing_soul and existing_soul.soul_id != soul_id:
            messages.error(request, "Registration could not be completed for this submission.")
            return redirect("souls:register_landing")

        soul_defaults = {
            "offering_soul": registration.offering_soul,
            "survey_response": registration.survey_response,
            "soul_id": soul_id,
            "survey_digest": survey_digest,
            "package": package,
            "package_bytes": None,
            "email_hash": email_hash,
            "email_verified_at": timezone.now(),
        }
        Soul.objects.update_or_create(user=user, defaults=soul_defaults)
        registration.state = SoulRegistrationSession.State.COMPLETED
        registration.save(update_fields=["state"])

    backend = _registration_auth_backend()
    if backend:
        login(request, user, backend=backend)
    request.session.pop(REG_SESSION_KEY, None)
    messages.success(request, "Soul Seed registration completed.")
    return redirect("souls:me")


@login_required
@require_GET
def soul_me(request: HttpRequest) -> HttpResponse:
    soul = get_object_or_404(Soul.objects.select_related("offering_soul"), user=request.user)
    return render(request, "souls/me.html", {"soul": soul})


@login_required
@require_GET
def soul_download(request: HttpRequest) -> HttpResponse:
    soul = get_object_or_404(Soul, user=request.user)
    data = soul.package_bytes or json.dumps(soul.package, sort_keys=True, separators=(",", ":")).encode("utf-8")
    response = HttpResponse(data, content_type="application/octet-stream")
    response["Content-Disposition"] = f'attachment; filename="{soul.soul_id}.soul"'
    return response


@login_required
@require_POST
def attach_to_checkout(request: HttpRequest) -> HttpResponse:
    soul = get_object_or_404(Soul, user=request.user, soul_id=request.POST.get("soul_id", ""))
    request.session[CHECKOUT_SOUL_KEY] = soul.id
    messages.success(request, "Soul Seed will be attached at checkout for Soul Card fulfillment.")
    return redirect("shop:checkout")
