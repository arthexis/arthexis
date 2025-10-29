import os
import sys
import tempfile
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

import shutil

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from pages.views import _render_markdown_with_toc


class ReadmeAssetMarkdownTests(TestCase):
    def test_static_scheme_rewritten(self):
        html, _ = _render_markdown_with_toc(
            "![Diagram](static://images/example.png)"
        )
        expected = reverse(
            "pages:readme-asset",
            kwargs={"source": "static", "asset": "images/example.png"},
        )
        self.assertIn(f'src="{expected}"', html)

    def test_work_scheme_rewritten(self):
        html, _ = _render_markdown_with_toc(
            "![Plot](work://reports/chart.webp)"
        )
        expected = reverse(
            "pages:readme-asset",
            kwargs={"source": "work", "asset": "reports/chart.webp"},
        )
        self.assertIn(f'src="{expected}"', html)


class ReadmeAssetViewTests(TestCase):
    def test_static_asset_served(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            asset_path = Path(tmpdir) / "example.png"
            asset_path.write_bytes(b"fake-image-data")
            with override_settings(STATICFILES_DIRS=[tmpdir]):
                url = reverse(
                    "pages:readme-asset",
                    kwargs={"source": "static", "asset": "example.png"},
                )
                response = self.client.get(url)
                content = b"".join(response.streaming_content)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(content, b"fake-image-data")

    def test_work_asset_requires_authentication(self):
        url = reverse(
            "pages:readme-asset",
            kwargs={"source": "work", "asset": "reports/example.png"},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_work_asset_served_for_owner(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="asset-user", password="test123")
        self.client.force_login(user)
        work_dir = Path(settings.BASE_DIR) / "work" / user.get_username()
        asset_path = work_dir / "reports" / "example.webp"
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_path.write_bytes(b"work-image-data")
        self.addCleanup(lambda: shutil.rmtree(work_dir, ignore_errors=True))

        url = reverse(
            "pages:readme-asset",
            kwargs={"source": "work", "asset": "reports/example.webp"},
        )
        response = self.client.get(url)
        content = b"".join(response.streaming_content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/webp")
        self.assertEqual(content, b"work-image-data")
