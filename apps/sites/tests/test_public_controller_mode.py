import re
from pathlib import Path

BASE_CSS = Path("apps/sites/static/pages/css/base.css")
BASE_JS = Path("apps/sites/static/pages/js/base.js")
FEEDBACK_JS = Path("apps/sites/static/pages/js/user_story_feedback.js")
PUBLIC_FEEDBACK_TEMPLATE = Path(
    "apps/sites/templates/pages/includes/public_feedback_widget.html"
)
DOCS_README_TEMPLATE = Path("apps/docs/templates/docs/readme.html")
READER_SCRIPT_TEMPLATE = Path("apps/docs/templates/includes/reader_qr_script.html")


def test_controller_mode_adds_large_targets_and_legacy_focus_fallbacks():
    css = BASE_CSS.read_text(encoding="utf-8")
    script = BASE_JS.read_text(encoding="utf-8")

    assert "setupControllerMode();" in script
    assert "PlayStation 4" in script
    assert "['controller', 'tv', 'ps4']" in script
    assert "['0', 'false', 'off', 'no']" in script
    assert "decodeQueryPart" in script
    assert "catch (error)" in script
    assert ".controller-mode .navbar .nav-link" in css
    assert ".controller-mode .user-story-rating label" in css
    assert ".controller-mode .markdown-body table" in css
    assert "a:focus," in css
    assert ".reader-table-toggle:focus," in css
    assert ":focus-visible" in css


def test_controller_gamepad_polling_handles_connected_slots_and_disconnects():
    script = BASE_JS.read_text(encoding="utf-8")

    assert "getActiveGamepad" in script
    assert "!navigator.getGamepads && !navigator.webkitGetGamepads" in script
    assert "return navigator.webkitGetGamepads();" in script
    assert "Array.from(getGamepads() || []).find(gamepad => gamepad && gamepad.buttons)" in script
    assert "Array.from(gamepad.buttons)" in script
    assert "isButtonPressed(button)" in script
    assert "typeof button === 'number'" in script
    assert "typeof button.value === 'number'" in script
    assert "clearPressedState();" in script
    assert "pressedButtons.clear();" in script
    assert "gamepadconnected" in script
    assert "gamepaddisconnected" in script


def test_controller_zoom_uses_document_origin_focus_closest_and_tokens():
    css = BASE_CSS.read_text(encoding="utf-8")
    script = BASE_JS.read_text(encoding="utf-8")

    assert "lastPointerX + (window.scrollX || window.pageXOffset || 0)" in script
    assert "lastPointerY + (window.scrollY || window.pageYOffset || 0)" in script
    assert "target.closest(focusSelector)" in script
    assert "--controller-zoom-scale" in css
    assert "--controller-zoom-origin-default-x" in css
    assert "--controller-zoom-transition" in css
    assert "transform: scale(var(--controller-zoom-scale));" in css
    assert ".controller-mode body" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "transition: none;" in css
    assert "createBubblingEvent('pages:feedback-toggle')" in script
    assert "document.createEvent('Event')" in script
    assert "createMouseMoveEvent(lastPointerX, lastPointerY)" in script
    assert "document.createEvent('MouseEvent')" in script
    assert ".initMouseEvent(" in script


def test_docs_reader_has_controller_safe_full_document_and_reload_paths():
    template = DOCS_README_TEMPLATE.read_text(encoding="utf-8")

    visible_link_index = template.index("reader-full-document-link")
    noscript_index = template.index("<noscript>")
    assert visible_link_index < noscript_index
    assert 'reader-full-document-link" href="{{ full_document_url }}" hidden' in template
    assert 'removeAttribute("hidden")' in template
    assert 'href="{{ full_document_url }}"' in template
    assert '"0", "false", "off", "no"' in template
    assert 'key === "controller" || key === "tv" || key === "ps4"' in template
    assert "var decodeQueryPart = function" in template
    assert "catch (error)" in template
    assert "30 * 60 * 1000" in template
    assert '"pointermove"' in template
    assert '"focusin"' in template
    assert 'body.classList.contains("user-story-open")' in template


def test_reader_tables_are_focusable_for_controller_scrolling():
    script = READER_SCRIPT_TEMPLATE.read_text(encoding="utf-8")

    assert "table.tabIndex = 0;" in script
    assert 'table.setAttribute("aria-label", headingText);' in script
    assert "?." not in script


def test_public_feedback_dialog_traps_focus_and_keeps_visible_rating_focus():
    script = FEEDBACK_JS.read_text(encoding="utf-8")
    template = PUBLIC_FEEDBACK_TEMPLATE.read_text(encoding="utf-8")
    css = BASE_CSS.read_text(encoding="utf-8")

    assert "trapOverlayFocus(event);" in script
    assert "selectRatingByValue" in script
    assert "typeof window.AbortController === 'function'" in script
    assert "data-feedback-close aria-label" in template
    assert ".user-story-rating input:focus + label" in css


def test_public_scripts_avoid_selected_modern_syntax_in_ps4_paths():
    scripts = [
        BASE_JS.read_text(encoding="utf-8"),
        FEEDBACK_JS.read_text(encoding="utf-8"),
        READER_SCRIPT_TEMPLATE.read_text(encoding="utf-8"),
    ]

    for script in scripts:
        assert "?." not in script
        assert re.search(r"\basync\b|\bawait\b", script) is None


def test_local_css_keeps_public_pages_readable_without_bootstrap_cdn():
    css = BASE_CSS.read_text(encoding="utf-8")
    base_template = Path("apps/sites/templates/pages/base.html").read_text(
        encoding="utf-8"
    )

    assert "--bs-body-bg: #ffffff;" in css
    assert "--bs-border-color-translucent" in css
    assert ":where(.container)" in css
    assert ":where(.btn)" in css
    assert "{% static 'pages/css/base.css' %}" in base_template
