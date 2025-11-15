from django.urls import path
from . import views


app_name = "pages"

urlpatterns = [
    path("", views.index, name="index"),
    path("docs/", views.readme_docs_redirect, name="docs-redirect"),
    path("docs/<path:doc>", views.readme_docs_redirect, name="docs-document-redirect"),
    path(
        "read/assets/<str:source>/<path:asset>",
        views.readme_asset,
        name="readme-asset",
    ),
    path("read/<path:doc>/edit/", views.readme_edit, name="readme-edit"),
    path("read/", views.readme, name="readme"),
    path("read/<path:doc>", views.readme, name="readme-document"),
    path("articles/<slug:slug>/", views.developer_article_detail, name="developer-article"),
    path("sitemap.xml", views.sitemap, name="pages-sitemap"),
    path("changelog/", views.changelog_report, name="changelog"),
    path("changelog/data/", views.changelog_report_data, name="changelog-data"),
    path("release/", views.release_admin_redirect, name="release-admin"),
    path("client-report/", views.client_report, name="client-report"),
    path(
        "client-report/download/<int:report_id>/",
        views.client_report_download,
        name="client-report-download",
    ),
    path("release-checklist", views.release_checklist, name="release-checklist"),
    path("login/rfid/", views.rfid_login_page, name="rfid-login"),
    path(
        "login/authenticator/check/",
        views.authenticator_login_check,
        name="authenticator-login-check",
    ),
    path("login/", views.login_view, name="login"),
    path("passkeys/login/options/", views.passkey_login_options, name="passkey-login-options"),
    path("passkeys/login/verify/", views.passkey_login_verify, name="passkey-login-verify"),
    path(
        "passkeys/register/options/",
        views.passkey_register_options,
        name="passkey-register-options",
    ),
    path(
        "passkeys/register/verify/",
        views.passkey_register_verify,
        name="passkey-register-verify",
    ),
    path("passkeys/<int:pk>/delete/", views.passkey_delete, name="passkey-delete"),
    path("authenticator/setup/", views.authenticator_setup, name="authenticator-setup"),
    path("request-invite/", views.request_invite, name="request-invite"),
    path(
        "invitation/<uidb64>/<token>/",
        views.invitation_login,
        name="invitation-login",
    ),
    path("man/", views.manual_list, name="manual-list"),
    path("man/<slug:slug>/", views.manual_detail, name="manual-detail"),
    path("man/<slug:slug>/pdf/", views.manual_pdf, name="manual-pdf"),
    path("feedback/user-story/", views.submit_user_story, name="user-story-submit"),
]
