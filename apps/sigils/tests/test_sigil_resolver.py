import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType

from apps.sigils import sigil_resolver
from apps.sigils.models import SigilRoot


@pytest.mark.django_db
def test_resolve_sigils_unknown_root_returns_placeholder():
    result = sigil_resolver.resolve_sigils("Value: [UNKNOWN.key]")

    assert result.endswith("[UNKNOWN.key]")


@pytest.mark.django_db
def test_resolve_sigils_env_normalizes_key(monkeypatch):
    SigilRoot.objects.update_or_create(
        prefix="ENV", defaults={"context_type": SigilRoot.Context.CONFIG}
    )
    monkeypatch.setenv("EXAMPLE_VAR", "42")

    result = sigil_resolver.resolve_sigils("[env.example-var]")

    assert result == "42"


@pytest.fixture
def user_root():
    user_model = get_user_model()
    root, _ = SigilRoot.objects.update_or_create(
        prefix="USR",
        defaults={
            "context_type": SigilRoot.Context.ENTITY,
            "content_type": ContentType.objects.get_for_model(user_model),
        },
    )
    return root


@pytest.mark.django_db
def test_resolve_sigils_filters_and_fetches_field(user_root):
    user_model = get_user_model()
    user = user_model.objects.create(username="SigilUser", email="sigil@example.com")

    result = sigil_resolver.resolve_sigils("[USR:username=sigiluser.email]")

    assert result == user.email


@pytest.mark.django_db
def test_resolve_sigils_aggregates_count(user_root):
    user_model = get_user_model()
    user_model.objects.create(username="user1")
    user_model.objects.create(username="user2")

    result = sigil_resolver.resolve_sigils("[USR=:count]")

    assert result == "2"
