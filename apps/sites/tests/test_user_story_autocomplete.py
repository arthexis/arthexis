import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse


@pytest.mark.django_db
def test_user_story_autocomplete_uses_standard_model_for_anonymous(client, monkeypatch):
    from apps.sites import autocomplete

    def fake_suggest(self, *, text, is_staff, limit):
        assert text == "needs faster"
        assert is_staff is False
        assert limit == 5
        return ["response", "time"]

    monkeypatch.setattr(autocomplete.FeedbackAutocompleteHarness, "suggest", fake_suggest)

    response = client.get(reverse("pages:user-story-autocomplete"), {"q": "needs faster"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["model"] == "standard"
    assert payload["suggestions"] == ["response", "time"]


@pytest.mark.django_db
def test_user_story_autocomplete_accepts_post_body_for_anonymous(client, monkeypatch):
    from apps.sites import autocomplete

    def fake_suggest(self, *, text, is_staff, limit):
        assert text == "sensitive draft"
        assert is_staff is False
        assert limit == 4
        return ["detail"]

    monkeypatch.setattr(autocomplete.FeedbackAutocompleteHarness, "suggest", fake_suggest)

    response = client.post(
        reverse("pages:user-story-autocomplete"),
        {"q": "sensitive draft", "limit": "4"},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["model"] == "standard"
    assert payload["suggestions"] == ["detail"]


@pytest.mark.django_db
def test_user_story_autocomplete_uses_repo_trained_model_for_staff(client, monkeypatch):
    from apps.sites import autocomplete

    user = get_user_model().objects.create_user(
        username="ops-staff",
        password="pass12345",
        is_staff=True,
    )
    client.force_login(user)

    def fake_suggest(self, *, text, is_staff, limit):
        assert text == "status panel"
        assert is_staff is True
        assert limit == 3
        return ["dashboard", "operator"]

    monkeypatch.setattr(autocomplete.FeedbackAutocompleteHarness, "suggest", fake_suggest)

    response = client.get(
        reverse("pages:user-story-autocomplete"),
        {"q": "status panel", "limit": "3"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["model"] == "repo-trained"
    assert payload["suggestions"] == ["dashboard", "operator"]


def test_standard_autocomplete_suggests_next_phrase_word():
    from apps.sites.autocomplete import FeedbackAutocompleteHarness

    harness = FeedbackAutocompleteHarness()

    assert harness.suggest(text="The page", is_staff=False, limit=3) == ["loaded"]
    assert harness.suggest(text="The page ", is_staff=False, limit=3) == ["loaded"]
    assert harness.suggest(text="The page lo", is_staff=False, limit=3) == ["loaded"]
    assert "loaded" not in harness.suggest(text="lo", is_staff=False, limit=3)


def test_standard_autocomplete_does_not_use_summarizer_fallback():
    from apps.sites.autocomplete import FeedbackAutocompleteHarness

    harness = FeedbackAutocompleteHarness()

    assert harness.suggest(text="unmatched context", is_staff=False, limit=3) == []


def test_repo_autocomplete_uses_one_cached_scan_for_model_and_common(monkeypatch):
    from apps.sites import autocomplete

    calls = 0

    def fake_streams():
        nonlocal calls
        calls += 1
        yield ["status", "panel", "status", "message"]

    autocomplete._repo_stats.cache_clear()
    monkeypatch.setattr(autocomplete, "_iter_repo_token_streams", fake_streams)

    try:
        assert autocomplete._repo_token_model()["status"] == ["panel", "message"]
        assert autocomplete._repo_common_tokens()[0] == "status"
        assert calls == 1
    finally:
        autocomplete._repo_stats.cache_clear()


def test_repo_autocomplete_completes_active_staff_token(monkeypatch):
    from apps.sites import autocomplete

    autocomplete._repo_stats.cache_clear()
    monkeypatch.setattr(
        autocomplete,
        "_repo_stats",
        lambda: (
            {"status": ["panel", "message"], "panel": ["operator"]},
            ["status", "panel"],
        ),
    )

    harness = autocomplete.FeedbackAutocompleteHarness()

    assert harness.suggest(text="status pa", is_staff=True, limit=3) == ["panel", "status"]
    assert harness.suggest(text="status ", is_staff=True, limit=3) == [
        "panel",
        "message",
        "status",
    ]


def test_repo_token_streams_excludes_generated_and_dependency_dirs(tmp_path, settings):
    from apps.sites import autocomplete

    (tmp_path / "visible.py").write_text("alpha beta", encoding="utf-8")
    for directory in (".git", ".venv", "node_modules"):
        nested = tmp_path / directory
        nested.mkdir()
        (nested / "ignored.py").write_text("hidden token", encoding="utf-8")

    settings.BASE_DIR = tmp_path

    assert list(autocomplete._iter_repo_token_streams()) == [["alpha", "beta"]]


def test_repo_token_streams_prunes_excluded_dirs_before_scanning(
    tmp_path,
    settings,
    monkeypatch,
):
    from apps.sites import autocomplete

    visible = tmp_path / "visible"
    visible.mkdir()
    (visible / "story.py").write_text("alpha beta", encoding="utf-8")
    blocked = tmp_path / "node_modules"
    blocked.mkdir()
    (blocked / "ignored.py").write_text("hidden token", encoding="utf-8")

    settings.BASE_DIR = tmp_path

    def fake_walk(path, onerror=None):
        names = ["node_modules", "visible"]
        yield path, names, []
        if "node_modules" in names:
            yield blocked, [], ["ignored.py"]
        if "visible" in names:
            yield visible, [], ["story.py"]

    monkeypatch.setattr(autocomplete.os, "walk", fake_walk)

    assert list(autocomplete._iter_repo_token_streams()) == [["alpha", "beta"]]
