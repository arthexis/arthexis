from __future__ import annotations

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase

from utils.extensions import (
    ExtensionError,
    activate_extension_paths,
    load_declared_extension_repositories,
    load_extension_manifests,
    normalize_github_repository,
)


MANIFEST = """\
[extension]
name = "diagnostics"
repository = "arthexis/arthexis-diagnostics"
django_apps = ["arthexis_diagnostics"]
requires_apps = ["apps.core"]
"""


class ExtensionDiscoveryTests(SimpleTestCase):
    def test_discovers_manifest_and_activates_checkout_path(self):
        with TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            checkout = base_dir / "extensions" / "arthexis-diagnostics"
            checkout.mkdir(parents=True)
            (checkout / "arthexis-extension.toml").write_text(
                MANIFEST,
                encoding="utf-8",
            )

            manifests = load_extension_manifests(base_dir)

            self.assertEqual(len(manifests), 1)
            self.assertEqual(manifests[0].name, "diagnostics")
            self.assertEqual(manifests[0].django_apps, ("arthexis_diagnostics",))
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
                '[extensions]\ndiagnostics = "arthexis/arthexis-diagnostics"\n',
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"ARTHEXIS_EXTENSIONS": "telemetry,other/example"},
                clear=False,
            ):
                declarations = load_declared_extension_repositories(base_dir)

        self.assertEqual(
            declarations,
            {
                "diagnostics": "arthexis/arthexis-diagnostics",
                "example": "other/example",
                "telemetry": "arthexis/arthexis-telemetry",
            },
        )

    def test_normalizes_short_names_and_github_urls(self):
        self.assertEqual(
            normalize_github_repository("diagnostics"),
            "arthexis/arthexis-diagnostics",
        )
        self.assertEqual(
            normalize_github_repository(
                "https://github.com/example/arthexis-tools.git"
            ),
            "example/arthexis-tools",
        )
        with self.assertRaises(ExtensionError):
            normalize_github_repository("example/..")
