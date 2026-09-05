"""Targeted tests for imager service module seams."""

import shlex
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse

import pytest
from django.test import override_settings

from apps.imager import services as imager_services
from apps.imager.services import artifacts, build_engine
from apps.imager.services.guestfish import (
    _expand_root_partition_to_image,
    _guestfish_symlink_command,
    _guestfish_upload_commands,
)
from apps.imager.services.models import ImagerBuildError
from apps.imager.services.network_profiles import select_host_network_profiles
from apps.imager.services.source import (
    _normalize_local_source_path,
    _validate_remote_base_image_url,
)


def test_artifact_helpers_keep_build_engine_and_package_compatibility_aliases() -> None:
    helper_names = (
        "_build_download_uri",
        "_build_local_http_url",
        "_build_profile_manifest",
        "_build_served_artifact_url",
        "_coerce_profile_metadata",
        "_format_url_host",
        "_image_size_metadata",
        "_network_profiles_metadata",
        "_reservation_metadata",
        "_sanitize_storage_options",
        "_sha256_for_file",
        "_sha256_for_prefix",
        "_suite_bundle_metadata",
    )

    for name in helper_names:
        assert getattr(build_engine, name) is getattr(artifacts, name)
        assert getattr(imager_services, name) is getattr(artifacts, name)


def test_source_normalizes_file_uri_with_localhost() -> None:
    parsed = urlparse("file://localhost/tmp/base%20image.img")

    assert _normalize_local_source_path(
        "file://localhost/tmp/base%20image.img", parsed
    ) == Path("/tmp/base image.img")


@override_settings(
    IMAGER_ALLOWED_REMOTE_IMAGE_HOSTS=(), IMAGER_BLOCK_PRIVATE_REMOTE_IMAGE_HOSTS=True
)
def test_source_validation_blocks_literal_private_host() -> None:
    with pytest.raises(ImagerBuildError, match="blocked non-public address"):
        _validate_remote_base_image_url("https://127.0.0.1/base.img")


@override_settings(
    IMAGER_ALLOWED_REMOTE_IMAGE_HOSTS=("127.0.0.1",),
    IMAGER_BLOCK_PRIVATE_REMOTE_IMAGE_HOSTS=True,
)
def test_source_validation_allows_explicit_allowlist() -> None:
    _validate_remote_base_image_url("https://127.0.0.1/base.img")


def test_guestfish_upload_and_partition_commands_are_constructed() -> None:
    local_path = Path("/tmp/local file")

    assert _guestfish_upload_commands(
        local_path,
        "/etc/example file",
        chmod_mode="0600",
    ) == [
        f"upload {shlex.quote(str(local_path))} '/etc/example file'",
        "chmod 0600 '/etc/example file'",
    ]
    assert _guestfish_symlink_command(
        target="/etc/service", link_path="/etc/wants/service"
    ) == ("ln-sf /etc/service /etc/wants/service")

    scripts: list[str] = []

    def capture_script(_image_path: Path, script: str, *, error_message: str) -> None:
        scripts.append(script)

    with (
        patch("apps.imager.services.guestfish._ensure_guestfish"),
        patch(
            "apps.imager.services.guestfish._run_guestfish_raw_script",
            side_effect=capture_script,
        ),
    ):
        _expand_root_partition_to_image(Path("/tmp/image.img"), end_sector=12345)

    assert scripts == [
        "run\npart-resize /dev/sda 2 12345\nblockdev-rereadpt /dev/sda\ne2fsck-f /dev/sda2\nresize2fs /dev/sda2\n"
    ]


def test_network_profile_selection_matches_id_and_deduplicates_filenames(
    tmp_path: Path,
) -> None:
    first = tmp_path / "shop wifi"
    first.write_text("[connection]\nid=shop-floor\n", encoding="utf-8")
    second = tmp_path / "uplink.nmconnection"
    second.write_text("[connection]\nid=uplink\n", encoding="utf-8")
    (tmp_path / ".hidden").write_text("[connection]\nid=hidden\n", encoding="utf-8")

    profiles = select_host_network_profiles(
        profile_dir=tmp_path, names=["shop-floor", "uplink"]
    )

    assert [profile.name for profile in profiles] == [
        "shop-floor",
        "uplink",
        "arthexis-charger-eth0",
    ]
    assert [profile.filename for profile in profiles] == [
        "shop_wifi.nmconnection",
        "uplink.nmconnection",
        "arthexis-charger-eth0.nmconnection",
    ]
    assert [profile.remote_path for profile in profiles] == [
        "/etc/NetworkManager/system-connections/shop_wifi.nmconnection",
        "/etc/NetworkManager/system-connections/uplink.nmconnection",
        "/etc/NetworkManager/system-connections/arthexis-charger-eth0.nmconnection",
    ]
