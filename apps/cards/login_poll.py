from __future__ import annotations

import secrets

RFID_LOGIN_POLL_SESSION_KEY = "rfid_login_poll_token"
RFID_LOGIN_POLL_QUERY_PARAM = "login_poll"
RFID_LOGIN_POLL_HEADER = "X-RFID-Login-Poll"


def ensure_rfid_login_poll_token(request) -> str:
    session = getattr(request, "session", None)
    if session is None:
        return ""
    token = str(session.get(RFID_LOGIN_POLL_SESSION_KEY) or "")
    if not token:
        token = secrets.token_urlsafe(32)
        session[RFID_LOGIN_POLL_SESSION_KEY] = token
    return token


def request_has_rfid_login_poll_token(request) -> bool:
    session = getattr(request, "session", None)
    if session is None:
        return False
    expected = str(session.get(RFID_LOGIN_POLL_SESSION_KEY) or "")
    if not expected:
        return False
    supplied = (
        request.GET.get(RFID_LOGIN_POLL_QUERY_PARAM)
        or request.headers.get(RFID_LOGIN_POLL_HEADER)
        or ""
    )
    return secrets.compare_digest(expected, str(supplied))
