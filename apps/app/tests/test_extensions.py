from __future__ import annotations

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase

from utils.extensions import (
    activate_extension_paths,
    load_declared_extension_repositories,
    load_extension_manifests,
    normalize_github_repository,
)


MANIFEST = """\
[extension]
name = "printers"
repository = "arthexis/arthexis-printers"
django_apps = ["arthexis_printers"]
requires_apps = ["apps.core"]
feature_packs = ["printer_workflows"]
suite_features = ["printer-workflows"]
"""


class ExtensionDiscoveryTests(SimpleTestCase):
    def test_discovers_manifest_and_activates_checkout_path(self):
        with TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            checkout = base_dir / "extensions" / "arthexis-printers"
            checkout.mkdir(parents=True)
            (checkout / "arthexis-extension.toml").write_text(
                MANIFEST,
                encoding="utf-8",
            )

            manifests = load_extension_manifests(base_dir)

            self.assertEqual(len(manifests), 1)
            self.assertEqual(manifests[0].name, "printers")
            self.assertEqual(manifests[0].django_apps, ("arthexis_printers",))
            self.assertEqual(manifests[0].requires_apps, ("apps.core",))

            original_sys_path = list(sys.path)
            try:
                activate_extension_paths(base_dir)
                self.assertEqual(sys.path[0], str(checkout))
            finally:
                sys.path[:] = original_sys_path

    def test_loads_file_and_environment_declarations(self):
        with TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            extensions = base_dir / "extensions"
            extensions.mkdir()
            (extensions / "extensions.toml").write_text(
                '[extensions]\nprinters = "arthexis/arthexis-printers"\n',
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"ARTHEXIS_EXTENSIONS": "widgets,other/example"},
                clear=False,
            ):
                declarations = load_declared_extension_repositories(base_dir)

        self.assertEqual(
            declarations,
            {
                "example": "other/example",
                "printers": "arthexis/arthexis-printers",
                "widgets": "arthexis/arthexis-widgets",
            },
        )

    def test_normalizes_short_names_and_github_urls(self):
        self.assertEqual(
            normalize_github_repository("printers"),
            "arthexis/arthexis-printers",
        )
        self.assertEqual(
            normalize_github_repository(
                "https://github.com/example/arthexis-tools.git"
            ),
            "example/arthexis-tools",
        )
