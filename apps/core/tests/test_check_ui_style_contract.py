from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from scripts.check_ui_style_contract import run_check


class CheckUiStyleContractTests(SimpleTestCase):
    def test_reports_inline_style_and_unknown_class(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            template_path = root / "apps/demo/templates/admin/demo/page.html"
            template_path.parent.mkdir(parents=True, exist_ok=True)
            template_path.write_text(
                '<div class="foo-thing"></div><style>.foo-thing{color:red;}</style>',
                encoding="utf-8",
            )

            index_path = root / "docs/development/ui-style-index.md"
            index_path.parent.mkdir(parents=True, exist_ok=True)
            index_path.write_text("# UI style index\n\n- `admin-ui-panel`\n", encoding="utf-8")

            violations = run_check(repo_root=root, index_path=index_path)

        codes = {violation.code for violation in violations}
        self.assertIn("INLINE_STYLE_DISALLOWED", codes)
        self.assertIn("UNKNOWN_PREFIX", codes)
        self.assertIn("NEW_CLASS_NOT_INDEXED", codes)

    def test_respects_waivers_and_allow_custom_css_marker(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            template_path = root / "apps/demo/templates/admin/demo/page.html"
            template_path.parent.mkdir(parents=True, exist_ok=True)
            template_path.write_text(
                "\n".join(
                    [
                        "{# admin-ui-framework: allow-custom-css #}",
                        "{# ui-style-contract: waive-new-class foo-thing #}",
                        '<div class="foo-thing"></div>',
                        "<style>.foo-thing{color:red;}</style>",
                    ]
                ),
                encoding="utf-8",
            )

            index_path = root / "docs/development/ui-style-index.md"
            index_path.parent.mkdir(parents=True, exist_ok=True)
            index_path.write_text("# UI style index\n\n- `admin-ui-panel`\n", encoding="utf-8")

            violations = run_check(repo_root=root, index_path=index_path)

        self.assertEqual([], violations)
