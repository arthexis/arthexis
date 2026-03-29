import hashlib
import logging

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.utils import OperationalError, ProgrammingError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import translation
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.docs import rendering
from apps.energy.models import CustomerAccount
from apps.features.utils import get_cached_feature_enabled, get_cached_feature_parameter
from apps.locale.models import Language
from apps.ocpp.models import PublicConnectorPage, PublicScanEvent, Transaction
from apps.ocpp.views.common import (
    _charger_state,
    _clear_stale_statuses_for_view,
    _connector_set,
    _default_language_code,
    _ensure_charger_access,
    _get_charger,
    _live_sessions,
    _reverse_connector_url,
    _supported_language_codes,
    _visible_error_code,
)
from apps.sites.utils import get_request_language_code
from apps.users.backends import LocalhostAdminBackend

logger = logging.getLogger(__name__)
ENERGY_ACCOUNTS_FEATURE_SLUG = "energy-accounts"
PUBLIC_CONNECTOR_PAGE_URL_NAME = "ocpp:public-connector-page"


class PublicConnectorAccountCreateForm(forms.Form):
    """Minimal signup form for public connector pages."""

    username = forms.CharField(max_length=150)
    email = forms.EmailField(required=False)
    password = forms.CharField(widget=forms.PasswordInput())


def _energy_accounts_enabled() -> bool:
    """Return whether energy account-first routing is enabled."""

    return get_cached_feature_enabled(
        ENERGY_ACCOUNTS_FEATURE_SLUG,
        cache_key="feature-enabled:energy-accounts",
        timeout=300,
        default=False,
    )


def _signup_auth_backend() -> str | None:
    """Return a safe auth backend path for post-signup login."""

    localhost_backend = f"{LocalhostAdminBackend.__module__}.{LocalhostAdminBackend.__name__}"
    for backend in settings.AUTHENTICATION_BACKENDS:
        if backend != localhost_backend:
            return backend

    model_backend = "django.contrib.auth.backends.ModelBackend"
    if model_backend in settings.AUTHENTICATION_BACKENDS:
        logger.warning(
            "No signup-safe auth backend found; using configured ModelBackend fallback."
        )
        return model_backend

    logger.warning("No signup-safe auth backend found; skipping automatic login.")
    return None


def _energy_credits_required() -> bool:
    """Return whether positive credits are required for account auth."""

    return (
        get_cached_feature_parameter(
            ENERGY_ACCOUNTS_FEATURE_SLUG,
            "energy_credits_required",
            cache_key="feature-parameter:energy-accounts:energy_credits_required",
            timeout=300,
            fallback="disabled",
        )
        == "enabled"
    )


def _get_client_ip(request) -> str:
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        for value in forwarded_for.split(","):
            candidate = value.strip()
            if candidate:
                return candidate
    return request.META.get("REMOTE_ADDR", "")


def _hash_ip(value: str) -> str:
    secret = getattr(settings, "SECRET_KEY", "")
    payload = f"{value}:{secret}".encode()
    return hashlib.sha256(payload).hexdigest()


def public_connector_page(request, slug):
    """Public landing page for a connector QR code."""
    _clear_stale_statuses_for_view()
    page = get_object_or_404(
        PublicConnectorPage.objects.select_related("charger", "language"),
        slug=slug,
        enabled=True,
    )
    charger = page.charger
    energy_accounts_enabled = _energy_accounts_enabled()
    if not charger.public_display or not charger.is_visible_to(request.user):
        raise Http404("Public page not found")
    if energy_accounts_enabled and request.user.is_authenticated:
        destination = _reverse_connector_url(
            "charger-page",
            charger.charger_id,
            charger.connector_slug,
        )
        return redirect(destination)

    connectors = _connector_set(charger)
    sessions = _live_sessions(charger, connectors=connectors)
    tx = None
    if charger.connector_id is not None and sessions:
        tx = sessions[0][1]
    state_source = (
        tx if charger.connector_id is not None else (sessions if sessions else None)
    )
    state, color = _charger_state(charger, state_source)

    instructions_html, _ = rendering.render_markdown_with_toc(
        page.instructions_markdown or ""
    )
    rules_html, _ = rendering.render_markdown_with_toc(page.rules_markdown or "")

    available_languages = _supported_language_codes()
    language_options = []
    try:
        languages_qs = Language.objects.filter(code__in=available_languages)
        for language in languages_qs:
            label = language.native_name or language.english_name or language.code
            language_options.append({"code": language.code, "label": label})
    except (OperationalError, ProgrammingError):
        language_options = [
            {"code": code, "label": code} for code in available_languages
        ]
    language_candidates = []
    page_language = page.language_code()
    if page_language:
        language_candidates.append(page_language)
    charger_language = charger.language_code()
    if charger_language:
        language_candidates.append(charger_language)
    request_language = get_request_language_code(request)
    if request_language:
        language_candidates.append(request_language)
    fallback_language = _default_language_code()
    if fallback_language:
        language_candidates.append(fallback_language)
    preferred_language = ""
    for code in language_candidates:
        if code in available_languages:
            preferred_language = code
            break
    if preferred_language:
        translation.activate(preferred_language)
        request.LANGUAGE_CODE = preferred_language

    try:
        ip_value = _get_client_ip(request)
        PublicScanEvent.objects.create(
            page=page,
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:255],
            referrer=(request.META.get("HTTP_REFERER") or "")[:2000],
            ip_hash=_hash_ip(ip_value) if ip_value else "",
        )
    except (OperationalError, ProgrammingError):
        pass

    return render(
        request,
        "energy/public_connector_page.html",
        {
            "account_create_form": PublicConnectorAccountCreateForm(),
            "energy_accounts_enabled": energy_accounts_enabled,
            "energy_credits_required": _energy_credits_required(),
            "next_url": _reverse_connector_url(
                "charger-page",
                charger.charger_id,
                charger.connector_slug,
            ),
            "page": page,
            "charger": charger,
            "state": state,
            "color": color,
            "instructions_html": instructions_html,
            "rules_html": rules_html,
            "available_languages": language_options,
            "preferred_language": preferred_language,
            "charger_error_code": _visible_error_code(charger.last_error_code),
        },
    )


@require_POST
def public_connector_page_create_account(request, slug):
    """Create a user + energy account from a public connector page."""

    page = get_object_or_404(
        PublicConnectorPage.objects.select_related("charger"),
        slug=slug,
        enabled=True,
    )
    charger = page.charger
    if not charger.public_display or not charger.is_visible_to(request.user):
        raise Http404("Public page not found")
    next_url = _reverse_connector_url(
        "charger-page",
        charger.charger_id,
        charger.connector_slug,
    )
    if not _energy_accounts_enabled():
        messages.error(request, _("Energy account onboarding is not enabled."))
        return redirect(PUBLIC_CONNECTOR_PAGE_URL_NAME, slug=slug)
    if request.user.is_authenticated:
        messages.error(request, _("Please sign out before creating a new account."))
        return redirect(PUBLIC_CONNECTOR_PAGE_URL_NAME, slug=slug)
    form = PublicConnectorAccountCreateForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Please provide valid account details."))
        return redirect(PUBLIC_CONNECTOR_PAGE_URL_NAME, slug=slug)

    username = form.cleaned_data["username"]
    email = form.cleaned_data.get("email", "")
    password = form.cleaned_data["password"]
    user_model = get_user_model()
    if user_model.objects.filter(username=username).exists():
        messages.error(request, _("Username is already in use."))
        return redirect(PUBLIC_CONNECTOR_PAGE_URL_NAME, slug=slug)

    try:
        with transaction.atomic():
            user = user_model.objects.create_user(
                username=username,
                email=email,
                password=password,
            )
            CustomerAccount.objects.get_or_create(
                user=user,
                defaults={"name": username.upper()},
            )
    except IntegrityError:
        messages.error(request, _("Username is already in use."))
        return redirect(PUBLIC_CONNECTOR_PAGE_URL_NAME, slug=slug)
    signup_backend = _signup_auth_backend()
    if signup_backend is not None:
        login(request, user, backend=signup_backend)
        messages.success(request, _("Account created. Charging authorization has been updated."))
    else:
        messages.warning(
            request,
            _("Account created, but you are not signed in. Please sign in to switch to the new account."),
        )
    return redirect(next_url)


@login_required
def charger_account_summary(request, cid, connector=None):
    """Show account balance and previous sessions for authenticated users."""

    charger, connector_slug = _get_charger(cid, connector)
    access_response = _ensure_charger_access(request.user, charger, request=request)
    if access_response is not None:
        return access_response
    account = CustomerAccount.objects.filter(user=request.user).first()
    if account is None:
        messages.warning(request, _("No energy account is attached to your user yet."))
    recent_sessions = (
        Transaction.objects.filter(account=account).select_related("charger").order_by("-start_time")[:20]
        if account is not None
        else []
    )
    return render(
        request,
        "energy/charger_account_summary.html",
        {
            "account": account,
            "charger": charger,
            "connector_slug": connector_slug,
            "energy_credits_required": _energy_credits_required(),
            "recent_sessions": recent_sessions,
        },
    )
