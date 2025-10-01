import pytest
from django.contrib.auth import REDIRECT_FIELD_NAME, get_user_model
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse

from utils.decorators import staff_required


pytestmark = pytest.mark.django_db


@pytest.fixture
def decorated_view():
    @staff_required
    def sample_view(request):
        return HttpResponse("ok")

    return sample_view


def test_staff_required_redirects_non_staff_user(decorated_view):
    rf = RequestFactory()
    request = rf.get("/protected/")
    request.user = get_user_model().objects.create_user(
        "regular",
        "regular@example.com",
        "password",
    )

    response = decorated_view(request)

    assert response.status_code == 302
    login_url = reverse("admin:login")
    expected_redirect = f"{login_url}?{REDIRECT_FIELD_NAME}=/protected/"
    assert response.url == expected_redirect


def test_staff_required_allows_staff_user(decorated_view):
    rf = RequestFactory()
    request = rf.get("/protected/")
    request.user = get_user_model().objects.create_user(
        "staff",
        "staff@example.com",
        "password",
        is_staff=True,
    )

    response = decorated_view(request)

    assert response.status_code == 200
    assert response.content == b"ok"


def test_staff_required_marks_view_with_attributes(decorated_view):
    assert getattr(decorated_view, "login_required") is True
    assert getattr(decorated_view, "staff_required") is True
