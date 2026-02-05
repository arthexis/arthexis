from __future__ import annotations

import logging
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import DisallowedHost, PermissionDenied
from django.db import DatabaseError
from django.db.utils import OperationalError, ProgrammingError
from django.http.request import split_domain_port
from django.shortcuts import resolve_url
from django.urls import path as django_path
from django.utils.translation import get_language

from apps.celery.utils import celery_feature_enabled

try:  # pragma: no cover - compatibility shim for Django versions without constant
    from django.utils.translation import LANGUAGE_SESSION_KEY
except ImportError:  # pragma: no cover - fallback when constant is unavailable
    LANGUAGE_SESSION_KEY = "_language"


logger = logging.getLogger(__name__)

SITE_OPERATOR_GROUP_NAME = "Site Operator"


ORIGINAL_REFERER_SESSION_KEY = "pages:original_referer"
REFERRER_LANDING_SESSION_KEY = "pages:referrer_landing_id"


def _safe_session_get(session, key, default=None):
    """Return ``session[key]`` without raising backend errors."""

    try:
        return session.get(key, default)
    except (AttributeError, DatabaseError):
        return default
    except Exception:  # pragma: no cover - best effort guard
        logger.debug("Unable to read %s from session", key, exc_info=True)
        return default


def landing(label=None):
    """Decorator to mark a view as a landing page."""

    def decorator(view):
        view.landing = True
        view.landing_label = label or view.__name__.replace("_", " ").title()
        return view

    return decorator


def user_in_site_operator_group(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    try:
        return user.groups.filter(name=SITE_OPERATOR_GROUP_NAME).exists()
    except (OperationalError, ProgrammingError):
        return False


def require_site_operator_or_staff(request, *, login_url: str = "pages:login"):
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        return redirect_to_login(request.get_full_path(), resolve_url(login_url))
    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        return None
    if user_in_site_operator_group(user):
        return None
    raise PermissionDenied


def cache_original_referer(request) -> None:
    """Persist the first external referer observed for the session."""

    session = getattr(request, "session", None)
    if not hasattr(session, "get"):
        return

    original = _safe_session_get(session, ORIGINAL_REFERER_SESSION_KEY)

    if original:
        request.original_referer = original
        return

    referer = (request.META.get("HTTP_REFERER") or "").strip()
    if not referer:
        return

    try:
        parsed = urlsplit(referer)
    except ValueError:
        return

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return

    try:
        host = request.get_host()
    except DisallowedHost:
        host = ""

    referer_host, _ = split_domain_port(parsed.netloc)
    request_host, _ = split_domain_port(host)

    if referer_host and request_host:
        if referer_host.lower() == request_host.lower():
            return

    referer_value = referer[:1000]
    try:
        session[ORIGINAL_REFERER_SESSION_KEY] = referer_value
    except Exception:  # pragma: no cover - best effort guard
        logger.debug("Unable to cache original referer in session", exc_info=True)
        return
    request.original_referer = referer_value


def get_original_referer(request) -> str:
    """Return the original external referer recorded for the session."""

    if hasattr(request, "original_referer"):
        return request.original_referer or ""

    session = getattr(request, "session", None)
    if hasattr(session, "get"):
        referer = _safe_session_get(session, ORIGINAL_REFERER_SESSION_KEY, "")
        if referer:
            request.original_referer = referer
            return referer

    referer = (request.META.get("HTTP_REFERER") or "").strip()
    if referer:
        referer = referer[:1000]
    request.original_referer = referer
    return referer


def landing_leads_supported() -> bool:
    """Return ``True`` when the local node supports landing lead tracking."""

    from apps.nodes.models import Node

    node = Node.get_local()
    if not node:
        return False
    return celery_feature_enabled(node)


def get_request_language_code(request) -> str:
    """Return the preferred interface language for the given request."""

    language_code = ""
    session = getattr(request, "session", None)
    if hasattr(session, "get"):
        language_code = _safe_session_get(session, LANGUAGE_SESSION_KEY, "")
    if not language_code:
        cookie_name = getattr(settings, "LANGUAGE_COOKIE_NAME", "django_language")
        language_code = getattr(request, "COOKIES", {}).get(cookie_name, "")
    if not language_code:
        language_code = getattr(request, "LANGUAGE_CODE", "") or ""
    if not language_code:
        language_code = get_language() or ""

    language_code = (language_code or "").strip()
    if not language_code:
        return ""

    return language_code.replace("_", "-").lower()[:15]


def get_referrer_landing(request, site):
    """Return the stored or resolved referrer landing for the site."""

    if site is None:
        return None

    session = getattr(request, "session", None)
    stored_id = None
    if hasattr(session, "get"):
        stored_id = _safe_session_get(session, REFERRER_LANDING_SESSION_KEY)

    if stored_id:
        from .models import ReferrerLanding

        try:
            referrer_landing = ReferrerLanding.objects.select_related("landing").get(
                pk=stored_id,
                site=site,
                enabled=True,
                is_deleted=False,
            )
        except ReferrerLanding.DoesNotExist:
            _clear_referrer_landing_session(session)
        else:
            request.referrer_landing = referrer_landing
            return referrer_landing

    referer = get_original_referer(request)
    if not referer:
        return None

    try:
        from .models import ReferrerLanding

        referrer_landing = ReferrerLanding.objects.match_for_site(site, referer)
    except Exception:  # pragma: no cover - best effort guard
        logger.debug("Unable to resolve referrer landing", exc_info=True)
        return None

    if referrer_landing is None:
        return None

    _store_referrer_landing_session(session, referrer_landing.pk)
    request.referrer_landing = referrer_landing
    return referrer_landing


def _store_referrer_landing_session(session, referrer_landing_id: int) -> None:
    if not hasattr(session, "__setitem__"):
        return
    try:
        session[REFERRER_LANDING_SESSION_KEY] = referrer_landing_id
    except Exception:  # pragma: no cover - best effort guard
        logger.debug("Unable to store referrer landing in session", exc_info=True)


def _clear_referrer_landing_session(session) -> None:
    if not hasattr(session, "pop"):
        return
    try:
        session.pop(REFERRER_LANDING_SESSION_KEY, None)
    except Exception:  # pragma: no cover - best effort guard
        logger.debug("Unable to clear referrer landing from session", exc_info=True)
