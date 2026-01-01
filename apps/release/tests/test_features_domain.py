from __future__ import annotations

import pytest

from apps.release.domain import ReleaseFeature, ReleaseFeatures


def test_release_feature_normalization_and_slug():
    feature = ReleaseFeature(
        title="  Add API tokens  ",
        summary="Allow creating scoped tokens",
        category=" Feature ",
        ticket=" 1234 ",
        scope="users",
    )

    assert feature.title == "Add API tokens"
    assert feature.summary == "Allow creating scoped tokens"
    assert feature.category == "feature"
    assert feature.ticket == "1234"
    assert feature.scope == "users"
    assert feature.slug == "add-api-tokens"


def test_release_feature_from_mapping_and_formatting():
    source = {
        "title": "Restrict admin access",
        "summary": "Limit admin logins to SSO",
        "category": "security",
        "breaking": True,
    }
    feature = ReleaseFeature.from_mapping(source)

    assert feature.breaking is True
    assert feature.category == "security"
    assert "BREAKING" in feature.as_bullet()


def test_release_features_collection_helpers():
    features = ReleaseFeatures.from_iterable(
        version="1.2.3",
        features=[
            {"title": "Alpha", "summary": "New thing", "category": "feature"},
            {"title": "Beta", "summary": "Changed behavior", "breaking": True},
            {"title": "Gamma", "summary": "Security patch", "category": "security"},
        ],
    )

    assert len(features) == 3
    breaking = features.breaking_changes
    assert len(breaking) == 1
    assert breaking[0].title == "Beta"
    assert [item.title for item in features.by_category("security")] == ["Gamma"]
    formatted = features.format()
    assert formatted.startswith("-")
    assert "Gamma" in formatted


def test_release_feature_requires_title_and_summary():
    with pytest.raises(ValueError):
        ReleaseFeature(title="", summary="Summary")

    with pytest.raises(ValueError):
        ReleaseFeature(title="Title", summary="")
