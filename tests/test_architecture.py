from pathlib import Path

ROOT_TEST_ALLOWLIST = {
    "test_architecture.py",
    "test_readme.py",
}
TOP_LEVEL_PACKAGE_ALLOWLIST = {
    "apps",
    "arthexis",
    "deploy",
    "integration",
    "ocpp",
}
MIRRORED_SOURCE_ROOTS = {
    "apps": Path("apps"),
    "arthexis": Path("arthexis"),
}


def test_every_first_party_app_has_a_mirrored_test_package() -> None:
    app_names = {
        initializer.parent.name
        for initializer in Path("apps").glob("*/__init__.py")
    }

    for app_name in sorted(app_names):
        expected = Path("tests/apps") / app_name / "__init__.py"
        assert expected.is_file(), f"apps.{app_name} must have tests/apps/{app_name}/"


def test_mirrored_test_packages_have_matching_source_packages() -> None:
    for root_name, source_root in MIRRORED_SOURCE_ROOTS.items():
        test_root = Path("tests") / root_name
        for initializer in test_root.rglob("__init__.py"):
            relative_package = initializer.parent.relative_to(test_root)
            source_package = source_root / relative_package
            assert source_package.is_dir(), (
                f"{initializer.parent} mirrors no source package at {source_package}"
            )


def test_source_owned_top_level_test_packages_are_mirrored() -> None:
    top_level_packages = {
        path.name
        for path in Path("tests").iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    }

    assert top_level_packages == TOP_LEVEL_PACKAGE_ALLOWLIST


def test_root_test_modules_are_explicit_repository_contracts() -> None:
    root_tests = {
        path.name
        for path in Path("tests").glob("test_*.py")
    }

    assert root_tests == ROOT_TEST_ALLOWLIST


def test_explicit_non_mirror_buckets_exist() -> None:
    for bucket in ("deploy", "integration", "ocpp"):
        expected = Path("tests") / bucket / "__init__.py"
        assert expected.is_file(), f"tests/{bucket}/ must remain an explicit test package"


def test_conformance_suite_does_not_recreate_protocol_version_packages() -> None:
    assert not (Path("tests/ocpp") / "v16").exists()
    assert not (Path("tests/ocpp") / "v201").exists()
