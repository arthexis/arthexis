from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

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
        auto_upgrade = base_dir / "AUTO_UPGRADE"
        locks_dir = base_dir / "locks"
        logs_dir = base_dir / "logs"
        pre_locks = locks_dir.exists()
        try:
            auto_upgrade.write_text("version")
            locks_dir.mkdir(exist_ok=True)
            (locks_dir / "celery.lck").touch()
            logs_dir.mkdir(exist_ok=True)
            now = timezone.now()
            (logs_dir / "auto-upgrade.log").write_text(
                f"{now.isoformat()} check_github_updates triggered\n"
            )
            html = self._render(self.user)
            self.assertIn("fresh since", html)
        finally:
            if auto_upgrade.exists():
                auto_upgrade.unlink()
            celery_lock = locks_dir / "celery.lck"
            if celery_lock.exists():
                celery_lock.unlink()
            log_file = logs_dir / "auto-upgrade.log"
            if log_file.exists():
                log_file.unlink()
            if not pre_locks and locks_dir.exists():
                try:
                    locks_dir.rmdir()
                except OSError:
                    pass

    def test_does_not_show_fresh_since_without_auto_upgrade(self):
        base_dir = Path(settings.BASE_DIR)
        locks_dir = base_dir / "locks"
        pre_locks = locks_dir.exists()
        try:
            locks_dir.mkdir(exist_ok=True)
            (locks_dir / "celery.lck").touch()
            html = self._render(self.user)
            self.assertNotIn("fresh since", html)
        finally:
            celery_lock = locks_dir / "celery.lck"
            if celery_lock.exists():
                celery_lock.unlink()
            if not pre_locks and locks_dir.exists():
                try:
                    locks_dir.rmdir()
                except OSError:
                    pass
