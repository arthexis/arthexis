from __future__ import annotations

import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.system import _build_nginx_report
from scripts.helpers.render_nginx_default import generate_config


class NginxReportTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def _prepare_locks(self, base_dir: Path, *, mode: str, port: int) -> None:
        lock_dir = base_dir / "locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        (lock_dir / "nginx_mode.lck").write_text(mode)
        (lock_dir / "backend_port.lck").write_text(str(port))

    def test_build_nginx_report_matches_expected_file(self) -> None:
        base_dir = Path(self.tmpdir.name)
        self._prepare_locks(base_dir, mode="public", port=9000)

        expected = generate_config("public", 9000)
        site_path = base_dir / "arthexis.conf"
        site_path.write_text(expected)

        report = _build_nginx_report(base_dir=base_dir, site_path=site_path)

        self.assertFalse(report["differs"])
        self.assertEqual(report["expected_content"], expected.rstrip("\n"))
        self.assertEqual(report["actual_content"], expected.rstrip("\n"))
        self.assertEqual(report["mode"], "public")
        self.assertEqual(report["port"], 9000)
        self.assertEqual(report["expected_path"], site_path)

    def test_nginx_report_view_renders(self) -> None:
        base_dir = Path(self.tmpdir.name)
        self._prepare_locks(base_dir, mode="internal", port=1234)

        expected = generate_config("internal", 1234)
        site_path = base_dir / "arthexis.conf"
        site_path.write_text(expected)

        User = get_user_model()
        admin_user = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password123",
        )
        self.client.force_login(admin_user)

        with self.settings(BASE_DIR=str(base_dir), NGINX_SITE_PATH=str(site_path)):
            response = self.client.get(reverse("admin:system-nginx-report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "NGINX Report")
        self.assertContains(response, "Matches expected configuration")
        self.assertContains(response, str(site_path))
        self.assertContains(response, "proxy_pass http://127.0.0.1:1234;")
