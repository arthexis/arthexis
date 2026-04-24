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
