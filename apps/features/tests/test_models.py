"""Model-level regression coverage for suite feature lifecycle rules."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import call_command

from apps.features.models import Feature
from apps.features.parameters import get_feature_parameter_definitions
from apps.nodes.models import NodeFeature


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("metadata", "expected_count"),
    [
        ({"parameters": {"one": "1", "two": "2"}}, 2),
        ({"parameters": {}}, 0),
        ({"parameters": "not-a-dict"}, 0),
        ({"parameters": None}, 0),
        ({}, 0),
    ],
)
def test_params_count_reads_feature_metadata_parameters(
    metadata, expected_count: int
) -> None:
    """params_count should only count dictionary-backed parameter values."""

    feature = Feature.objects.create(
        slug="feature-params-count",
        display="Feature Params Count",
        metadata=metadata,
    )

    assert feature.params_count == expected_count


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("code_locations", "expected_app"),
    [
        (["apps/meta/views.py"], "meta"),
        (["apps/sites/views/management.py"], "pages"),
    ],
)
def test_feature_save_infers_main_app_from_code_locations(
    code_locations: list[str], expected_app: str
) -> None:
    """Features with app-prefixed code locations should auto-link main_app."""

    feature = Feature.objects.create(
        slug="app-inference",
        display="App Inference",
        code_locations=code_locations,
    )

    assert feature.main_app is not None
    assert feature.main_app.name == expected_app


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("code_locations", "expected_app"),
    [
        (["apps/meta/views.py"], "meta"),
        (["apps/sites/views/management.py"], "pages"),
    ],
)
def test_set_enabled_persists_inferred_main_app_with_update_fields(
    code_locations: list[str], expected_app: str
) -> None:
    """set_enabled should persist inferred main_app even with scoped update_fields."""

    feature = Feature.objects.create(
        slug="app-inference-update-fields",
        display="App Inference Update Fields",
        code_locations=[],
    )
    Feature.objects.filter(pk=feature.pk).update(code_locations=code_locations)
    feature.refresh_from_db()

    assert feature.main_app_id is None
    assert feature.set_enabled(False, update_fields=["is_enabled"]) is True

    feature.refresh_from_db()
    assert feature.main_app is not None
    assert feature.main_app.name == expected_app


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("fixture_name", "slug"),
    [
        ("features__feedback_ingestion.json", "feedback-ingestion"),
        ("features__nfc_login.json", "nfc-login"),
        ("features__operator_site_interface.json", "operator-site-interface"),
        ("features__pages_chat.json", "pages-chat"),
        ("features__staff_chat_bridge.json", "staff-chat-bridge"),
    ],
)
def test_pages_feature_fixtures_load_after_register_site_apps(
    fixture_name: str, slug: str
) -> None:
    """Pages-backed feature fixtures should resolve main_app against the pages app label."""

    fixture_path = (
        Path(settings.BASE_DIR) / "apps" / "features" / "fixtures" / fixture_name
    )

    call_command("register_site_apps")
    call_command("loaddata", str(fixture_path))

    feature = Feature.objects.get(slug=slug)
    assert feature.main_app is not None
    assert feature.main_app.name == "pages"


@pytest.mark.django_db
def test_llm_summary_suite_fixture_links_summary_node_feature() -> None:
    """The suite gate should unlock summary generation, not LCD hardware."""

    NodeFeature.objects.get_or_create(
        slug="llm-summary",
        defaults={"display": "LLM Summary"},
    )
    fixture_path = (
        Path(settings.BASE_DIR)
        / "apps"
        / "features"
        / "fixtures"
        / "features__llm_summary_suite.json"
    )

    call_command("register_site_apps")
    call_command("loaddata", str(fixture_path))

    feature = Feature.objects.select_related("node_feature").get(
        slug="llm-summary-suite"
    )
    assert feature.node_feature is not None
    assert feature.node_feature.slug == "llm-summary"


@pytest.mark.django_db
def test_kindle_postbox_fixture_links_docs_node_feature() -> None:
    """The suite feature should describe the docs-owned Kindle writer path."""

    NodeFeature.objects.get_or_create(
        slug="kindle-postbox",
        defaults={"display": "Kindle Postbox"},
    )
    fixture_path = (
        Path(settings.BASE_DIR)
        / "apps"
        / "features"
        / "fixtures"
        / "features__kindle_postbox.json"
    )

    call_command("register_site_apps")
    call_command("loaddata", str(fixture_path))

    feature = Feature.objects.select_related("node_feature").get(
        slug="kindle-postbox"
    )
    assert feature.main_app is not None
    assert feature.main_app.name == "docs"
    assert feature.node_feature is not None
    assert feature.node_feature.slug == "kindle-postbox"


def test_llm_summary_suite_exposes_context_window_parameters() -> None:
    keys = {
        definition.key
        for definition in get_feature_parameter_definitions("llm-summary-suite")
    }

    assert {"min_context_minutes", "max_context_minutes"} <= keys
