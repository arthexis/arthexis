from django.urls import path
from . import views


app_name = "pages"

urlpatterns = [
    path("", views.index, name="index"),
    path("read/<path:doc>/edit/", views.readme_edit, name="readme-edit"),
    path("read/", views.readme, name="readme"),
    path("read/<path:doc>", views.readme, name="readme-document"),
    path("sitemap.xml", views.sitemap, name="pages-sitemap"),
    path("release/", views.release_admin_redirect, name="release-admin"),
    path("client-report/", views.client_report, name="client-report"),
    path("release-checklist", views.release_checklist, name="release-checklist"),
    path("login/", views.login_view, name="login"),
    path("authenticator/setup/", views.authenticator_setup, name="authenticator-setup"),
    path("request-invite/", views.request_invite, name="request-invite"),
    path(
        "invitation/<uidb64>/<token>/",
        views.invitation_login,
        name="invitation-login",
    ),
    path("datasette-auth/", views.datasette_auth, name="datasette-auth"),
    path("man/", views.manual_list, name="manual-list"),
    path("man/<slug:slug>/", views.manual_detail, name="manual-detail"),
    path("man/<slug:slug>/pdf/", views.manual_pdf, name="manual-pdf"),
    path("feedback/user-story/", views.submit_user_story, name="user-story-submit"),
]
