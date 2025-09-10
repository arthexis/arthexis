from django.urls import path
from . import views


app_name = "pages"

urlpatterns = [
    path("", views.index, name="index"),
    path("sitemap.xml", views.sitemap, name="pages-sitemap"),
    path("energy-report/", views.energy_report, name="energy-report"),
    path("login/", views.login_view, name="login"),
    path("request-invite/", views.request_invite, name="request-invite"),
    path(
        "invitation/<uidb64>/<token>/",
        views.invitation_login,
        name="invitation-login",
    ),
    path("datasette-auth/", views.datasette_auth, name="datasette-auth"),
]
