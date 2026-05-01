from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

WHATSAPP_WEB_URL = "https://web.whatsapp.com/"
DEFAULT_WHATSAPP_WEB_PROFILE_DIR = (
    Path.home() / ".codex" / "whatsapp-web" / "playwright-profile"
)
DEFAULT_WHATSAPP_WEB_BROWSER = "edge" if sys.platform == "win32" else "firefox"
DEFAULT_WHATSAPP_WEB_CHANNEL = "msedge" if sys.platform == "win32" else ""
WHATSAPP_WEB_CURSOR_FILENAME = "message-cursors.json"

LOGGED_IN_SELECTORS = (
    "#pane-side",
    "#side",
    "[aria-label='Chat list']",
    "[aria-label='Lista de chats']",
    "[aria-label='Chats']",
    "[aria-label='Nuevo chat']",
    "[aria-label='New chat']",
    "[data-testid='chat-list']",
    "[data-icon='new-chat-outline']",
)
LOGIN_REQUIRED_SELECTORS = (
    "[data-testid='qrcode']",
    "canvas[aria-label*='Scan']",
    "canvas[aria-label*='Escanea']",
    "div[data-ref] canvas",
)
LOGIN_REQUIRED_TEXT = (
    "Use WhatsApp on your computer",
    "Usa WhatsApp en tu computadora",
    "Link with phone number",
    "Vincular con el numero de telefono",
    "Vincular con el número de teléfono",
)
USE_HERE_TEXT = ("Use here", "Usar aqui", "Usar aquí")
MESSAGE_COMPOSER_SELECTOR = (
    "footer [contenteditable='true'], "
    "div[aria-label='Escribe un mensaje'], "
    "div[aria-label='Type a message'], "
    "[contenteditable='true'][role='textbox']"
)
SEND_BUTTON_SELECTOR = "button[aria-label*='Enviar'], button[aria-label*='Send']"
SEND_ICON_SELECTOR = "[data-icon='send']"


class WhatsAppWebLoginStatus:
    """Known WhatsApp Web login validation states."""

    LOGGED_IN = "logged_in"
    LOGIN_REQUIRED = "login_required"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class WhatsAppWebLoginResult:
    """Result from a local WhatsApp Web login validation probe."""

    status: str
    profile_dir: Path
    elapsed_seconds: float
    url: str = ""
    marker: str = ""
    detail: str = ""


@dataclass(frozen=True)
class WhatsAppWebSendResult:
    status: str
    phone: str
    profile_dir: Path
    elapsed_seconds: float
    url: str = ""
    detail: str = ""


@dataclass(frozen=True)
class WhatsAppWebMessage:
    fingerprint: str
    index: int
    direction: str
    sender: str
    timestamp_raw: str
    timestamp_iso: str
    text: str


@dataclass(frozen=True)
class WhatsAppWebReadResult:
    status: str
    phone: str
    profile_dir: Path
    messages: list[WhatsAppWebMessage]
    cursor_updated: bool = False
    cursor_file: Path | None = None
    detail: str = ""


def dataclass_payload(value) -> dict[str, object]:
    payload = asdict(value)
    for key, item in list(payload.items()):
        if isinstance(item, Path):
            payload[key] = str(item)
        elif isinstance(item, list):
            payload[key] = [
                asdict(child) if hasattr(child, "__dataclass_fields__") else child
                for child in item
            ]
        elif item is None:
            payload[key] = None
    return payload


def normalize_whatsapp_phone(value: str, *, default_country_code: str = "52") -> str:
    """Return a WhatsApp Web URL phone identifier.

    A 10-digit local number is interpreted using the default country code.
    """

    raw = (value or "").strip()
    if raw.startswith("+"):
        digits = re.sub(r"\D+", "", raw)
    else:
        digits = re.sub(r"\D+", "", raw)
        if digits.startswith("00"):
            digits = digits[2:]
    if len(digits) == 10 and default_country_code:
        digits = f"{default_country_code}{digits}"
    if len(digits) < 8:
        raise ValueError("WhatsApp phone number is too short.")
    return digits


def parse_cli_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Expected YYYY-MM-DD date, got {value!r}.") from exc


def cursor_file_for_profile(profile_dir: Path | str | None = None) -> Path:
    resolved = Path(profile_dir or DEFAULT_WHATSAPP_WEB_PROFILE_DIR).expanduser()
    return resolved.parent / WHATSAPP_WEB_CURSOR_FILENAME


def _first_visible_locator(page, selectors: tuple[str, ...], *, timeout_ms: int) -> str:
    for selector in selectors:
        try:
            if page.locator(selector).first.is_visible(timeout=timeout_ms):
                return selector
        except Exception:
            continue
    return ""


def _first_visible_text(page, texts: tuple[str, ...], *, timeout_ms: int) -> str:
    for text in texts:
        try:
            if page.get_by_text(text, exact=False).first.is_visible(timeout=timeout_ms):
                return text
        except Exception:
            continue
    return ""


def detect_whatsapp_web_login_state(page, *, timeout_ms: int = 250) -> tuple[str, str]:
    """Return the visible WhatsApp Web login state for the current page."""

    marker = _first_visible_locator(
        page, LOGGED_IN_SELECTORS, timeout_ms=timeout_ms
    )
    if marker:
        return WhatsAppWebLoginStatus.LOGGED_IN, marker

    marker = _first_visible_locator(
        page, LOGIN_REQUIRED_SELECTORS, timeout_ms=timeout_ms
    )
    if marker:
        return WhatsAppWebLoginStatus.LOGIN_REQUIRED, marker

    marker = _first_visible_text(page, LOGIN_REQUIRED_TEXT, timeout_ms=timeout_ms)
    if marker:
        return WhatsAppWebLoginStatus.LOGIN_REQUIRED, marker

    return WhatsAppWebLoginStatus.UNKNOWN, ""


def accept_use_here_prompt(page) -> bool:
    """Click WhatsApp's one-window takeover prompt when it is visible."""

    for text in USE_HERE_TEXT:
        try:
            locator = page.get_by_text(text, exact=True).first
            if locator.is_visible(timeout=300):
                locator.click(timeout=2_000)
                return True
        except Exception:
            continue
    return False


def _playwright_runtime_help(exc: Exception, *, browser: str) -> str:
    base_message = str(exc)
    lower_message = base_message.lower()

    if "executable doesn't exist" in lower_message or "browser has been closed" in lower_message:
        install_browser = "firefox" if browser == "firefox" else "chromium"
        return (
            f"{base_message}\n"
            "The Playwright browser runtime is incomplete for this interpreter. "
            f"Run `{sys.executable} -m playwright install {install_browser}`."
        )

    if "host system is missing dependencies" in lower_message:
        return (
            f"{base_message}\n"
            "Playwright browser binaries exist, but OS dependencies are missing. "
            "Install the missing dependencies for this machine."
        )

    return base_message


def _ensure_windows_subprocess_event_loop_policy() -> None:
    """Use a Windows asyncio policy that supports Playwright subprocesses."""

    if sys.platform != "win32":
        return

    policy_cls = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
    if policy_cls is None:
        return
    if isinstance(asyncio.get_event_loop_policy(), policy_cls):
        return
    asyncio.set_event_loop_policy(policy_cls())


def _resolve_profile_dir(profile_dir: Path | str | None) -> Path:
    resolved = Path(profile_dir or DEFAULT_WHATSAPP_WEB_PROFILE_DIR).expanduser()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _resolve_browser(browser: str) -> str:
    resolved = (browser or DEFAULT_WHATSAPP_WEB_BROWSER).strip().lower()
    aliases = {
        "msedge": "edge",
        "edge": "edge",
        "firefox": "firefox",
        "chromium": "chromium",
        "chrome": "chromium",
    }
    if resolved not in aliases:
        raise ValueError("Browser must be one of: edge, firefox, chromium.")
    return aliases[resolved]


def _launch_persistent_context(
    playwright,
    *,
    profile_dir: Path,
    browser: str,
    channel: str,
    headless: bool,
):
    launch_kwargs = {
        "headless": headless,
        "viewport": {"width": 1280, "height": 900},
    }
    if browser == "firefox":
        return playwright.firefox.launch_persistent_context(
            str(profile_dir), **launch_kwargs
        )
    if browser == "edge":
        launch_kwargs["channel"] = channel or "msedge"
    elif channel:
        launch_kwargs["channel"] = channel
    return playwright.chromium.launch_persistent_context(
        str(profile_dir), **launch_kwargs
    )


def _with_whatsapp_page(
    callback,
    *,
    profile_dir: Path | str | None = None,
    browser: str = "",
    channel: str = "",
    headless: bool = False,
    timeout_seconds: float = 120.0,
    cdp_url: str = "",
):
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Playwright is required for WhatsApp Web automation. "
            f"Install it for this interpreter ({sys.executable})."
        ) from exc

    resolved_browser = _resolve_browser(browser)
    resolved_profile_dir = _resolve_profile_dir(profile_dir)
    timeout_seconds = max(float(timeout_seconds), 1.0)

    try:
        _ensure_windows_subprocess_event_loop_policy()
        with sync_playwright() as playwright:
            if cdp_url:
                connected_browser = playwright.chromium.connect_over_cdp(cdp_url)
                context = connected_browser.contexts[0]
                page = next(
                    (
                        candidate
                        for candidate in context.pages
                        if "web.whatsapp.com" in candidate.url
                    ),
                    context.pages[0] if context.pages else context.new_page(),
                )
                return callback(page, resolved_profile_dir, timeout_seconds)

            context = _launch_persistent_context(
                playwright,
                profile_dir=resolved_profile_dir,
                browser=resolved_browser,
                channel=channel.strip(),
                headless=headless,
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                return callback(page, resolved_profile_dir, timeout_seconds)
            finally:
                context.close()
    except (PlaywrightError, PlaywrightTimeoutError) as exc:
        raise RuntimeError(_playwright_runtime_help(exc, browser=resolved_browser)) from exc
    except Exception as exc:
        raise RuntimeError(_playwright_runtime_help(exc, browser=resolved_browser)) from exc


def _wait_for_login_state(
    page,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> tuple[str, str, float]:
    started_at = time.monotonic()
    deadline = started_at + max(float(timeout_seconds), 1.0)
    poll_interval_seconds = max(float(poll_interval_seconds), 0.2)
    last_login_required_marker = ""

    while time.monotonic() < deadline:
        accept_use_here_prompt(page)
        status, marker = detect_whatsapp_web_login_state(page)
        if status == WhatsAppWebLoginStatus.LOGGED_IN:
            return status, marker, time.monotonic() - started_at
        if status == WhatsAppWebLoginStatus.LOGIN_REQUIRED:
            last_login_required_marker = marker
        page.wait_for_timeout(int(poll_interval_seconds * 1000))

    elapsed = time.monotonic() - started_at
    if last_login_required_marker:
        return WhatsAppWebLoginStatus.LOGIN_REQUIRED, last_login_required_marker, elapsed
    return WhatsAppWebLoginStatus.TIMEOUT, "", elapsed


def validate_whatsapp_web_login(
    *,
    profile_dir: Path | str | None = None,
    timeout_seconds: float = 120.0,
    poll_interval_seconds: float = 1.0,
    headless: bool = False,
    browser: str = "",
    channel: str = "",
    cdp_url: str = "",
) -> WhatsAppWebLoginResult:
    """Open WhatsApp Web with a persistent profile and validate login state."""

    def run(page, resolved_profile_dir: Path, resolved_timeout: float):
        page.goto(
            WHATSAPP_WEB_URL,
            wait_until="domcontentloaded",
            timeout=int(resolved_timeout * 1000),
        )
        status, marker, elapsed = _wait_for_login_state(
            page,
            timeout_seconds=resolved_timeout,
            poll_interval_seconds=poll_interval_seconds,
        )
        if status == WhatsAppWebLoginStatus.LOGGED_IN:
            detail = "WhatsApp Web logged-in UI detected."
        elif status == WhatsAppWebLoginStatus.LOGIN_REQUIRED:
            detail = "WhatsApp Web login screen was visible until timeout."
        else:
            detail = "Timed out before detecting logged-in or login-required UI."
        return WhatsAppWebLoginResult(
            status=status,
            profile_dir=resolved_profile_dir,
            elapsed_seconds=elapsed,
            url=page.url,
            marker=marker,
            detail=detail,
        )

    return _with_whatsapp_page(
        run,
        profile_dir=profile_dir,
        browser=browser,
        channel=channel,
        headless=headless,
        timeout_seconds=timeout_seconds,
        cdp_url=cdp_url,
    )


def send_whatsapp_web_message(
    *,
    phone: str,
    message: str,
    default_country_code: str = "52",
    profile_dir: Path | str | None = None,
    timeout_seconds: float = 120.0,
    poll_interval_seconds: float = 1.0,
    headless: bool = False,
    browser: str = "",
    channel: str = "",
    cdp_url: str = "",
) -> WhatsAppWebSendResult:
    normalized_phone = normalize_whatsapp_phone(
        phone, default_country_code=default_country_code
    )
    message = (message or "").strip()
    if not message:
        raise ValueError("Message cannot be blank.")
    target_url = f"{WHATSAPP_WEB_URL}send?phone={normalized_phone}&text={quote(message)}"

    def run(page, resolved_profile_dir: Path, resolved_timeout: float):
        started_at = time.monotonic()
        page.goto(target_url, wait_until="domcontentloaded", timeout=int(resolved_timeout * 1000))
        status, _marker, _elapsed = _wait_for_login_state(
            page,
            timeout_seconds=resolved_timeout,
            poll_interval_seconds=poll_interval_seconds,
        )
        if status != WhatsAppWebLoginStatus.LOGGED_IN:
            return WhatsAppWebSendResult(
                status=status,
                phone=normalized_phone,
                profile_dir=resolved_profile_dir,
                elapsed_seconds=time.monotonic() - started_at,
                url=page.url,
                detail="WhatsApp Web is not logged in.",
            )

        page.locator(MESSAGE_COMPOSER_SELECTOR).last.click(timeout=15_000)
        page.locator(MESSAGE_COMPOSER_SELECTOR).last.fill(message, timeout=15_000)
        page.wait_for_timeout(500)
        if page.locator(SEND_ICON_SELECTOR).count() > 0:
            page.locator(SEND_ICON_SELECTOR).first.click(timeout=15_000)
        elif page.locator(SEND_BUTTON_SELECTOR).count() > 0:
            page.locator(SEND_BUTTON_SELECTOR).first.click(timeout=15_000)
        else:
            page.locator(MESSAGE_COMPOSER_SELECTOR).last.press("Enter", timeout=15_000)
        page.wait_for_timeout(1_000)
        return WhatsAppWebSendResult(
            status="sent",
            phone=normalized_phone,
            profile_dir=resolved_profile_dir,
            elapsed_seconds=time.monotonic() - started_at,
            url=page.url,
            detail="Message submitted through WhatsApp Web.",
        )

    return _with_whatsapp_page(
        run,
        profile_dir=profile_dir,
        browser=browser,
        channel=channel,
        headless=headless,
        timeout_seconds=timeout_seconds,
        cdp_url=cdp_url,
    )


def _parse_pre_plain_text(value: str) -> tuple[str, str]:
    match = re.match(r"^\[(?P<stamp>[^\]]+)\]\s*(?P<sender>.*?):\s*$", value or "")
    if not match:
        return "", ""
    return match.group("stamp").strip(), match.group("sender").strip()


def _parse_whatsapp_timestamp(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in (
        "%H:%M, %Y-%m-%d",
        "%I:%M %p, %Y-%m-%d",
        "%H:%M, %d/%m/%Y",
        "%H:%M, %m/%d/%Y",
        "%I:%M %p, %d/%m/%Y",
        "%I:%M %p, %m/%d/%Y",
        "%d/%m/%Y, %H:%M",
        "%m/%d/%Y, %H:%M",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _message_fingerprint(
    *,
    phone: str,
    direction: str,
    sender: str,
    timestamp_raw: str,
    text: str,
    index: int,
) -> str:
    data = "\x1f".join([phone, direction, sender, timestamp_raw, text, str(index)])
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def build_whatsapp_web_messages(
    rows: list[dict[str, object]],
    *,
    phone: str,
) -> list[WhatsAppWebMessage]:
    messages: list[WhatsAppWebMessage] = []
    for index, row in enumerate(rows):
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        timestamp_raw, sender = _parse_pre_plain_text(str(row.get("pre") or ""))
        parsed = _parse_whatsapp_timestamp(timestamp_raw)
        timestamp_iso = parsed.isoformat() if parsed is not None else ""
        direction = str(row.get("direction") or "unknown")
        fingerprint = _message_fingerprint(
            phone=phone,
            direction=direction,
            sender=sender,
            timestamp_raw=timestamp_raw,
            text=text,
            index=index,
        )
        messages.append(
            WhatsAppWebMessage(
                fingerprint=fingerprint,
                index=index,
                direction=direction,
                sender=sender,
                timestamp_raw=timestamp_raw,
                timestamp_iso=timestamp_iso,
                text=text,
            )
        )
    return messages


def filter_whatsapp_web_messages(
    messages: list[WhatsAppWebMessage],
    *,
    since: date | None = None,
    until: date | None = None,
    after_fingerprint: str = "",
) -> list[WhatsAppWebMessage]:
    filtered = messages
    if after_fingerprint:
        for index, message in enumerate(filtered):
            if message.fingerprint == after_fingerprint:
                filtered = filtered[index + 1 :]
                break
    if since or until:
        dated_messages: list[WhatsAppWebMessage] = []
        for message in filtered:
            if not message.timestamp_iso:
                continue
            message_date = datetime.fromisoformat(message.timestamp_iso).date()
            if since and message_date < since:
                continue
            if until and message_date > until:
                continue
            dated_messages.append(message)
        filtered = dated_messages
    return filtered


def _read_cursor(cursor_file: Path, key: str) -> str:
    try:
        payload = json.loads(cursor_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return ""
    return str(payload.get(key) or "")


def _write_cursor(cursor_file: Path, key: str, value: str) -> None:
    cursor_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = json.loads(cursor_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        payload = {}
    payload[key] = value
    cursor_file.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def read_whatsapp_web_messages(
    *,
    phone: str,
    default_country_code: str = "52",
    since: date | None = None,
    until: date | None = None,
    only_new: bool = False,
    update_cursor: bool = True,
    limit: int = 50,
    profile_dir: Path | str | None = None,
    timeout_seconds: float = 120.0,
    poll_interval_seconds: float = 1.0,
    headless: bool = False,
    browser: str = "",
    channel: str = "",
    cdp_url: str = "",
) -> WhatsAppWebReadResult:
    normalized_phone = normalize_whatsapp_phone(
        phone, default_country_code=default_country_code
    )
    target_url = f"{WHATSAPP_WEB_URL}send?phone={normalized_phone}"

    def run(page, resolved_profile_dir: Path, resolved_timeout: float):
        page.goto(target_url, wait_until="domcontentloaded", timeout=int(resolved_timeout * 1000))
        status, _marker, _elapsed = _wait_for_login_state(
            page,
            timeout_seconds=resolved_timeout,
            poll_interval_seconds=poll_interval_seconds,
        )
        if status != WhatsAppWebLoginStatus.LOGGED_IN:
            return WhatsAppWebReadResult(
                status=status,
                phone=normalized_phone,
                profile_dir=resolved_profile_dir,
                messages=[],
                detail="WhatsApp Web is not logged in.",
            )
        page.locator(MESSAGE_COMPOSER_SELECTOR).last.wait_for(timeout=15_000)
        rows = page.evaluate(
            """
            () => Array.from(document.querySelectorAll('div[data-pre-plain-text]'))
                .map((node) => {
                    const container = node.closest('.message-in, .message-out') || node;
                    const direction = container.classList.contains('message-out')
                        ? 'out'
                        : (container.classList.contains('message-in') ? 'in' : 'unknown');
                    const spans = Array.from(node.querySelectorAll(
                        'span.selectable-text, span[dir="auto"], span[dir="ltr"]'
                    ));
                    const text = spans.length
                        ? spans.map((span) => span.innerText || span.textContent || '').join('\\n')
                        : (node.innerText || node.textContent || '');
                    return {
                        pre: node.getAttribute('data-pre-plain-text') || '',
                        direction,
                        text,
                    };
                })
            """
        )
        all_messages = build_whatsapp_web_messages(rows, phone=normalized_phone)
        cursor_file = cursor_file_for_profile(resolved_profile_dir)
        cursor_key = f"{normalized_phone}:default"
        after_fingerprint = _read_cursor(cursor_file, cursor_key) if only_new else ""
        messages = filter_whatsapp_web_messages(
            all_messages,
            since=since,
            until=until,
            after_fingerprint=after_fingerprint,
        )
        if limit > 0:
            messages = messages[-limit:]
        cursor_updated = False
        if only_new and update_cursor and all_messages:
            _write_cursor(cursor_file, cursor_key, all_messages[-1].fingerprint)
            cursor_updated = True
        return WhatsAppWebReadResult(
            status="ok",
            phone=normalized_phone,
            profile_dir=resolved_profile_dir,
            messages=messages,
            cursor_updated=cursor_updated,
            cursor_file=cursor_file,
            detail=f"Read {len(messages)} visible messages from WhatsApp Web.",
        )

    return _with_whatsapp_page(
        run,
        profile_dir=profile_dir,
        browser=browser,
        channel=channel,
        headless=headless,
        timeout_seconds=timeout_seconds,
        cdp_url=cdp_url,
    )
