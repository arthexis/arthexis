from django.urls import path
from . import views


app_name = "pages"

urlpatterns = [
    path("", views.index, name="index"),
    path("footer/", views.footer_fragment, name="footer-fragment"),
    path("sitemap.xml", views.sitemap, name="pages-sitemap"),
    path("changelog/", views.changelog_report, name="changelog"),
    path("changelog/data/", views.changelog_report_data, name="changelog-data"),
    path("client-report/", views.client_report, name="client-report"),
    path(
        "client-report/download/<int:report_id>/",
        views.client_report_download,
        name="client-report-download",
    ),
    path("release-checklist", views.release_checklist, name="release-checklist"),
    path("login/rfid/", views.rfid_login_page, name="rfid-login"),
    path("login/", views.login_view, name="login"),
    path("webhooks/whatsapp/", views.whatsapp_webhook, name="whatsapp-webhook"),
    path("request-invite/", views.request_invite, name="request-invite"),
    path(
        "invitation/<uidb64>/<token>/",
        views.invitation_login,
        name="invitation-login",
    ),
    path("feedback/user-story/", views.submit_user_story, name="user-story-submit"),
]
