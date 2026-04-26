from pathlib import Path

from django.template.loader import render_to_string


def test_funding_banner_include_renders_supplied_layout_classes():
    html = render_to_string(
        "pages/includes/funding_banner.html",
        {
            "funding_banner": {
                "title": "Fund maintenance",
                "message": "Keep the project maintained.",
                "issue_url": "https://github.com/arthexis/arthexis/issues/1",
            },
            "funding_banner_classes": "funding-banner--sidebar mb-4",
        },
    )

    assert 'class="funding-banner funding-banner--sidebar mb-4"' in html
    assert 'href="https://github.com/arthexis/arthexis/issues/1"' in html
    assert "View funding issue" in html


def test_funding_banner_templates_share_single_include():
    base_template = Path("apps/sites/templates/pages/base.html").read_text()
    docs_template = Path("apps/docs/templates/docs/readme.html").read_text()

    assert 'include "pages/includes/funding_banner.html"' in base_template
    assert 'include "pages/includes/funding_banner.html"' in docs_template
    assert "funding-banner__mark" not in base_template
    assert "funding-banner__mark" not in docs_template


def test_funding_banner_css_uses_defined_tokens():
    css = Path("apps/sites/static/pages/css/base.css").read_text()
    funding_banner_css = css[css.index(".funding-banner {") :]

    assert "--bs-spacer-3" not in funding_banner_css
    assert "#9ec5fe" not in funding_banner_css
    assert "#cfe2ff" not in funding_banner_css
    assert "var(--bs-link-color)" in funding_banner_css
    assert "var(--bs-link-hover-color)" in funding_banner_css
