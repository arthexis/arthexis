from django.urls import path
from django.views.generic import RedirectView

from .views import analytics, landing, management


app_name = "pages"


class EngineeringBlogRedirectView(RedirectView):
    """Redirect retired engineering blog routes to the maintained changelog page."""

    pattern_name = "pages:changelog"
    permanent = True

    def get_redirect_url(self, *args, **kwargs):
        """Ignore legacy blog slug parameters when resolving the changelog destination."""

        del args, kwargs
        return super().get_redirect_url()


urlpatterns = [
    path("", landing.index, name="index"),
    path("footer/", landing.footer_fragment, name="footer-fragment"),
    path("operator-interface/", landing.operator_interface_notice, name="operator-interface-notice"),
    path("sitemap.xml", landing.sitemap, name="pages-sitemap"),
    path("changelog/", landing.changelog_report, name="changelog"),
    path("changelog/data/", landing.changelog_report_data, name="changelog-data"),
    path(
        "engineering/blog/",
        EngineeringBlogRedirectView.as_view(),
        name="engineering-blog-redirect",
    ),
    path(
        "engineering/blog/<path:slug>/",
        EngineeringBlogRedirectView.as_view(),
        name="engineering-blog-detail-redirect",
    ),
    path("client-report/", analytics.client_report, name="client-report"),
    path(
        "client-report/download/<int:report_id>/",
        analytics.client_report_download,
        name="client-report-download",
    ),
    path("release-checklist/", landing.release_checklist, name="release-checklist"),
    path(
        "release-checklist",
        RedirectView.as_view(pattern_name="pages:release-checklist", permanent=True),
    ),
    path("login/rfid/", management.rfid_login_page, name="rfid-login"),
    path("login/passkey/options/", management.passkey_login_options, name="passkey-login-options"),
    path("login/passkey/verify/", management.passkey_login_verify, name="passkey-login-verify"),
    path("login/", management.login_view, name="login"),
    path("logout/", management.logout_view, name="logout"),
    path("webhooks/whatsapp/", management.whatsapp_webhook, name="whatsapp-webhook"),
    path("request-invite/", management.request_invite, name="request-invite"),
    path(
        "invitation/<uidb64>/<token>/",
        management.invitation_login,
        name="invitation-login",
    ),
    path("feedback/user-story/", landing.submit_user_story, name="user-story-submit"),
]
