import re
from pathlib import Path

ADMIN_CSS = Path("apps/sites/static/sites/css/admin/base_site.css")
ADMIN_FEEDBACK_TEMPLATE = Path(
    "apps/sites/templates/admin/includes/user_story_feedback.html"
)
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
    assert (
        "Array.from(getGamepads() || []).find(gamepad => gamepad && gamepad.buttons)"
        in script
    )
    assert "Array.from(gamepad.buttons)" in script
    assert "isButtonPressed(button)" in script
    assert "typeof button === 'number'" in script
    assert "typeof button.value === 'number'" in script
    assert "clearPressedState();" in script
    assert "pressedButtons.clear();" in script
    assert "gamepadconnected" in script
    assert "gamepaddisconnected" in script


def test_module_pill_nav_draws_theme_based_wrap_separators():
    css = BASE_CSS.read_text(encoding="utf-8")
    script = BASE_JS.read_text(encoding="utf-8")
    base_template = Path("apps/sites/templates/pages/base.html").read_text(
        encoding="utf-8"
    )

    assert "data-module-pill-list" in base_template
    assert "setupNavbarWrapSeparators();" in script
    assert "navbar-nav-row-divider" in script
    assert "offsetTop" in script
    assert "offsetHeight" in script
    assert "ResizeObserver" in script
    assert "shown.bs.collapse" in script
    assert ".navbar-nav-wrap" in css
    assert "position: relative;" in css
    assert ".navbar-nav-row-divider" in css
    assert "var(--bs-border-color-translucent)" in css


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


def test_docs_readme_does_not_embed_an_incremental_document_viewer():
    template = DOCS_README_TEMPLATE.read_text(encoding="utf-8")

    assert "reader-remaining-loader" not in template
    assert "hx-get=" not in template


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
    assert re.search(r"\bAbortController\b", script) is None
    assert re.search(r"\buser-story-autocomplete\b", script) is None
    assert "data-autocomplete" not in template
    assert "data-feedback-close aria-label" in template
    assert "text-start mb-0" in template
    assert "user-story-feedback-submit-row" in template
    assert "user-story-submit" in template
    assert template.index("user-story-comments") < template.index(
        "user-story-rating-group-label"
    )
    assert template.index("user-story-rating-group-label") < template.index(
        "Submit feedback"
    )
    assert "You may contact me about this" in Path(
        "apps/sites/templates/pages/includes/user_story_contact_optin_checkbox.html"
    ).read_text(encoding="utf-8")
    assert ".user-story-rating input:focus + label" in css
    assert ".user-story-feedback-submit-row" in css
    assert "@media (min-width: 576px)" in css
    feedback_media_start = css.index("@media (min-width: 576px)")
    feedback_media_end = css.index("@media (min-width: 768px)", feedback_media_start)
    feedback_media_css = css[feedback_media_start:feedback_media_end]
    assert ".user-story-feedback-submit-row" in feedback_media_css
    assert "flex-direction: row;" in feedback_media_css
    assert "justify-content: space-between;" in feedback_media_css
    assert (
        ".user-story-submit {\n    flex: 0 0 auto;\n    width: auto;"
        in feedback_media_css
    )
    assert "display: block;\n  width: 100%;\n  transition: opacity 0.2s ease;" in css


def test_admin_feedback_dialog_spacing_matches_compact_header_layout():
    css = ADMIN_CSS.read_text(encoding="utf-8")
    template = ADMIN_FEEDBACK_TEMPLATE.read_text(encoding="utf-8")

    assert "margin: 0 0 var(--admin-ui-space-1, 0.25rem);" in css
    assert ".user-story-card .user-story-rating-field" in css
    assert "flex-direction: column;" in css
    assert "user-story-field user-story-rating-field" in template
    assert "You may contact me about this" in Path(
        "apps/sites/templates/admin/includes/user_story_contact_optin_checkbox.html"
    ).read_text(encoding="utf-8")
    assert 'aria-labelledby="user-story-rating-group-label"' in template


def test_public_feedback_dialog_caches_unsent_fields_per_browser_session_page():
    script = FEEDBACK_JS.read_text(encoding="utf-8")

    assert "window.sessionStorage" in script
    assert "arthexis:user-story-feedback:" in script
    assert "window.location.pathname" in script
    assert "window.location.search" in script
    assert "24 * 60 * 60 * 1000" in script
    assert "restoreFeedbackCache();" in script
    assert "writeFeedbackCache" in script
    assert "debouncedWriteFeedbackCache" in script
    assert "FEEDBACK_CACHE_WRITE_DELAY_MS = 500" in script
    assert "field.addEventListener('input', debouncedWriteFeedbackCache);" in script
    assert "field.addEventListener('change', writeFeedbackCache);" in script
    assert "clearTimeout(writeCacheTimeout);" in script
    assert "window.setTimeout(() => {" in script
    assert (
        "if (!window.fetch || !window.FormData) {\n      removeFeedbackCache();\n      return;\n    }"
        in script
    )
    assert "removeFeedbackCache();" in script
    assert "response.ok" in script


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
