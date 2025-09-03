from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch
from datetime import timedelta

from core.models import Package, PackageRelease


class FooterAdminLinkTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.package, _ = Package.objects.get_or_create(name="arthexis")
        self.release, _ = PackageRelease.objects.get_or_create(
            version="0.1.1", defaults={"package": self.package}
        )
        User = get_user_model()
        self.staff = User.objects.create_user(
            username="staff", password="pw", is_staff=True
        )
        self.user = User.objects.create_user(username="user", password="pw")

    def _render(self, user):
        request = self.factory.get("/")
        request.user = user
        tmpl = Template("{% load ref_tags %}{% render_footer %}")
        return tmpl.render(Context({"request": request}))

    def test_staff_sees_link_to_release_admin(self):
        html = self._render(self.staff)
        url = reverse("admin:core_packagerelease_change", args=[self.release.pk])
        self.assertIn(f'href="{url}"', html)

    def test_non_staff_does_not_see_link(self):
        html = self._render(self.user)
        url = reverse("admin:core_packagerelease_change", args=[self.release.pk])
        self.assertNotIn(f'href="{url}"', html)

    def test_shows_fresh_since_in_auto_upgrade_mode(self):
        base_dir = Path(settings.BASE_DIR)
        logs_dir = base_dir / "logs"
        logs_dir.mkdir(exist_ok=True)
        log_file = logs_dir / "auto-upgrade.log"
        try:
            now = timezone.now()
            log_file.write_text(f"{now.isoformat()} check_github_updates triggered\n")
            html = self._render(self.user)
            self.assertIn("fresh since", html)
        finally:
            if log_file.exists():
                log_file.unlink()

    def test_shows_fresh_since_without_auto_upgrade(self):
        html = self._render(self.user)
        self.assertIn("fresh since", html)

    def test_fresh_since_uses_latest_timestamp(self):
        base_dir = Path(settings.BASE_DIR)
        logs_dir = base_dir / "logs"
        logs_dir.mkdir(exist_ok=True)
        log_file = logs_dir / "auto-upgrade.log"
        now = timezone.now()
        log_file.write_text(
            f"{(now - timedelta(hours=2)).isoformat()} check_github_updates triggered\n"
        )
        try:
            with patch("core.templatetags.ref_tags.INSTANCE_START", now - timedelta(hours=1)):
                with patch("django.utils.timezone.now", return_value=now):
                    html = self._render(self.user)
            html = html.replace("\xa0", " ")
            self.assertIn("fresh since 1 hour", html)
        finally:
            if log_file.exists():
                log_file.unlink()
