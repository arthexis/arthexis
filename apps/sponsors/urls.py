"""URL patterns for sponsor registration."""

from django.urls import path

from . import views

app_name = "sponsors"

urlpatterns = [
    path("register/", views.SponsorRegistrationView.as_view(), name="register"),
    path(
        "register/thanks/",
        views.SponsorRegistrationThanksView.as_view(),
        name="register-thank-you",
    ),
]
