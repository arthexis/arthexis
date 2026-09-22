from pathlib import Path

from django.test import SimpleTestCase

ROOT_TEST_ALLOWLIST = {
    "test_architecture.py",
    "test_readme.py",
}


class TestTopologyTests(SimpleTestCase):
    def test_every_first_party_app_has_a_mirrored_test_package(self) -> None:
        app_names = {
            manifest.parent.name
            for manifest in Path("apps").glob("*/manifest.py")
        }

        for app_name in sorted(app_names):
            with self.subTest(app=app_name):
                self.assertTrue(
                    (Path("tests/apps") / app_name / "__init__.py").is_file(),
                    msg=f"apps.{app_name} must have tests/apps/{app_name}/",
                )

    def test_mirrored_test_packages_have_matching_source_packages(self) -> None:
        for root_name in ("apps", "arthexis"):
            test_root = Path("tests") / root_name
            source_root = Path(root_name)
            for initializer in test_root.rglob("__init__.py"):
                relative_package = initializer.parent.relative_to(test_root)
                source_package = source_root / relative_package
                with self.subTest(test_package=str(initializer.parent)):
                    self.assertTrue(
                        source_package.is_dir(),
                        msg=(
                            f"{initializer.parent} mirrors no source package "
                            f"at {source_package}"
                        ),
                    )

    def test_root_test_modules_are_explicit_repository_contracts(self) -> None:
        root_tests = {
            path.name
            for path in Path("tests").glob("test_*.py")
        }

        self.assertEqual(root_tests, ROOT_TEST_ALLOWLIST)

    def test_explicit_non_mirror_buckets_exist(self) -> None:
        for bucket in ("deploy", "integration", "ocpp"):
            with self.subTest(bucket=bucket):
                self.assertTrue((Path("tests") / bucket).is_dir())

    def test_conformance_suite_does_not_recreate_protocol_version_packages(
        self,
    ) -> None:
        self.assertFalse((Path("tests/ocpp") / "v16").exists())
        self.assertFalse((Path("tests/ocpp") / "v201").exists())
