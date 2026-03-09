"""Tests for the `evergo` management command."""

from __future__ import annotations

import io
from unittest.mock import Mock, patch

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.evergo.models import EvergoUser

@pytest.mark.django_db
@pytest.mark.integration
@patch("apps.evergo.models.user.EvergoUser._prime_session", return_value="xsrf-token")
@patch("apps.evergo.models.user.requests.Session")
def test_evergo_command_saves_credentials_and_tests_login(
    mock_session_cls, _mock_prime_session
):
    """Command should save credentials, test login, and sync API fields."""
    User = get_user_model()
    suite_user = User.objects.create_user(
        username="suite-user", email="suite@example.com"
    )

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": 100,
        "name": "Suite Evergo",
        "email": "suite.evergo@example.com",
        "two_fa_enabled": 1,
        "two_fa_authenticated": 1,
        "created_at": "2025-01-01T00:00:00.000000Z",
        "updated_at": "2025-01-02T00:00:00.000000Z",
        "subempresas": [{"id": 5, "idInstalaEmpresa": 2, "nombre": "Ops"}],
    }
    mock_session = Mock()
    mock_session.__enter__ = Mock(return_value=mock_session)
    mock_session.__exit__ = Mock(return_value=False)
    mock_session.post.return_value = mock_response
    mock_session_cls.return_value = mock_session

    stdout = io.StringIO()
    call_command(
        "evergo",
        suite_user.username,
        "--email",
        "suite.evergo@example.com",
        "--password",
        "secret",
        "--test",
        stdout=stdout,
    )

    profile = EvergoUser.objects.get(user=suite_user)
    assert profile.evergo_email == "suite.evergo@example.com"
    assert profile.evergo_user_id == 100
    assert "Evergo login successful" in stdout.getvalue()

@pytest.mark.django_db
def test_evergo_command_reuses_existing_profile_when_duplicates_exist():
    """Command should update the oldest profile when duplicate rows exist."""
    User = get_user_model()
    suite_user = User.objects.create_user(
        username="suite-dup", email="suite-dup@example.com"
    )
    first = EvergoUser.objects.create(user=suite_user, evergo_email="old@example.com")
    EvergoUser.objects.create(user=suite_user, evergo_email="newer@example.com")

    call_command(
        "evergo",
        suite_user.username,
        "--email",
        "resolved@example.com",
        "--password",
        "secret",
    )

    first.refresh_from_db()
    assert first.evergo_email == "resolved@example.com"
    assert EvergoUser.objects.filter(user=suite_user).count() == 2

@pytest.mark.django_db
def test_evergo_command_raises_for_ambiguous_user_identifier():
    """Command should reject identifiers that match different users by username/email."""
    User = get_user_model()
    User.objects.create_user(username="admin@example.com", email="owner1@example.com")
    User.objects.create_user(username="owner2", email="admin@example.com")

    with pytest.raises(CommandError, match="matches multiple users"):
        call_command(
            "evergo",
            "admin@example.com",
            "--email",
            "suite.evergo@example.com",
            "--password",
            "secret",
        )

@pytest.mark.django_db
@pytest.mark.integration
def test_evergo_command_load_customers_with_inline_queries():
    """Command should execute admin-equivalent customer load when requested."""
    User = get_user_model()
    suite_user = User.objects.create_user(username="suite-load", email="suite-load@example.com")
    profile = EvergoUser.objects.create(user=suite_user, evergo_email="ops@example.com", evergo_password="secret")

    stdout = io.StringIO()
    with patch.object(EvergoUser, "load_customers_from_queries") as mock_loader:
        mock_loader.return_value = {
            "sales_orders": ["J00123"],
            "customer_names": ["Acme"],
            "customers_loaded": 1,
            "orders_created": 2,
            "orders_updated": 3,
            "placeholders_created": 0,
            "unresolved": ["Acme"],
        }

        call_command(
            "evergo",
            suite_user.username,
            "--load-customers",
            "--queries",
            "J00123, Acme",
            "--timeout",
            "12",
            stdout=stdout,
        )

    mock_loader.assert_called_once_with(raw_queries="J00123, Acme", timeout=12)
    output = stdout.getvalue()
    assert "Customer sync completed" in output
    assert "Unresolved: Acme" in output
    profile.refresh_from_db()
    assert profile.evergo_email == "ops@example.com"

@pytest.mark.django_db
@pytest.mark.integration
def test_evergo_command_load_customers_requires_query_source():
    """Command should reject load-customer runs that do not provide any queries."""
    User = get_user_model()
    suite_user = User.objects.create_user(username="suite-missing", email="suite-missing@example.com")
    EvergoUser.objects.create(user=suite_user, evergo_email="ops@example.com", evergo_password="secret")

    with pytest.raises(CommandError, match="requires --queries or --queries-file"):
        call_command("evergo", suite_user.username, "--load-customers")

@pytest.mark.django_db
def test_evergo_command_load_customers_requires_existing_evergo_email():
    """Command should reject load-customer runs when the profile email is missing."""
    User = get_user_model()
    suite_user = User.objects.create_user(username="suite-email", email="suite-email@example.com")

    with pytest.raises(CommandError, match="missing evergo_email"):
        call_command(
            "evergo",
            suite_user.username,
            "--load-customers",
            "--queries",
            "J00123",
        )

@pytest.mark.django_db
@pytest.mark.integration
def test_evergo_command_load_customers_rejects_conflicting_query_sources(tmp_path):
    """Command should reject passing both inline and file query sources at once."""
    User = get_user_model()
    suite_user = User.objects.create_user(username="suite-conflict", email="suite-conflict@example.com")
    queries_file = tmp_path / "queries.txt"
    queries_file.write_text("J00123", encoding="utf-8")

    with pytest.raises(CommandError, match="Use only one of --queries or --queries-file"):
        call_command(
            "evergo",
            suite_user.username,
            "--load-customers",
            "--queries",
            "J00123",
            "--queries-file",
            str(queries_file),
        )

@pytest.mark.django_db
@pytest.mark.integration
def test_evergo_command_load_customers_supports_queries_file(tmp_path):
    """Command should read raw queries from a UTF-8 file and execute sync."""
    User = get_user_model()
    suite_user = User.objects.create_user(username="suite-file", email="suite-file@example.com")
    EvergoUser.objects.create(user=suite_user, evergo_email="ops@example.com", evergo_password="secret")
    queries_file = tmp_path / "queries.txt"
    queries_file.write_text("J00123\nBeta Customer", encoding="utf-8")

    with patch.object(EvergoUser, "load_customers_from_queries") as mock_loader:
        mock_loader.return_value = {
            "sales_orders": ["J00123"],
            "customer_names": ["Beta Customer"],
            "customers_loaded": 2,
            "orders_created": 1,
            "orders_updated": 1,
            "placeholders_created": 0,
            "unresolved": [],
        }

        call_command(
            "evergo",
            suite_user.username,
            "--load-customers",
            "--queries-file",
            str(queries_file),
        )

    mock_loader.assert_called_once_with(raw_queries="J00123\nBeta Customer", timeout=20)
