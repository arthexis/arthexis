"""Regression tests for Raspberry Pi imager workflows."""

from contextlib import nullcontext
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import call_command

from apps.imager.models import RaspberryPiImageArtifact
from apps.imager.services import ImagerBuildError, TARGET_RPI4B, build_rpi4b_image


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_build_command_prints_metadata(mock_build, tmp_path: Path) -> None:
    """Regression: imager build should print generated artifact metadata."""

    output_path = tmp_path / "artifact.img"
    output_path.write_bytes(b"pi")
    mock_build.return_value = type(
        "BuildResult",
        (),
        {
            "output_path": output_path,
            "sha256": "abc123",
            "size_bytes": 2,
            "download_uri": "https://downloads.example.com/artifact.img",
        },
    )()

    out = StringIO()
    call_command(
        "imager",
        "build",
        "--name",
        "v0-5-0",
        "--base-image-uri",
        str(output_path),
        stdout=out,
    )

    output = out.getvalue()
    assert "Built image:" in output
    assert "sha256=abc123" in output
    assert "download_uri=https://downloads.example.com/artifact.img" in output


@pytest.mark.django_db
def test_imager_list_command_prints_registered_artifacts() -> None:
    """Regression: imager list should render persisted artifact rows."""

    RaspberryPiImageArtifact.objects.create(
        name="nightly",
        target=TARGET_RPI4B,
        base_image_uri="https://example.com/rpi.img.xz",
        output_filename="nightly-rpi-4b.img",
        output_path="/tmp/nightly-rpi-4b.img",
        sha256="f" * 64,
        size_bytes=1024,
        download_uri="https://downloads.example.com/nightly-rpi-4b.img",
    )

    out = StringIO()
    call_command("imager", "list", stdout=out)
    output = out.getvalue()

    assert "nightly [rpi-4b]" in output
    assert "uri=https://downloads.example.com/nightly-rpi-4b.img" in output


@pytest.mark.django_db
def test_build_rpi4b_image_creates_artifact_with_download_uri(tmp_path: Path) -> None:
    """Regression: building an artifact should persist checksum and URI metadata."""

    base_image = tmp_path / "base.img"
    base_image.write_bytes(b"raspberrypi")

    with patch("apps.imager.services._customize_image"):
        result = build_rpi4b_image(
            name="stable",
            base_image_uri=str(base_image),
            output_dir=tmp_path,
            download_base_uri="https://cdn.example.com/images",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=True,
        )

    artifact = RaspberryPiImageArtifact.objects.get(name="stable")
    assert result.output_path.exists()
    assert artifact.sha256 == result.sha256
    assert artifact.download_uri == "https://cdn.example.com/images/stable-rpi-4b.img"


@pytest.mark.django_db
def test_build_rpi4b_image_rejects_unsafe_artifact_name(tmp_path: Path) -> None:
    """Regression: artifact names should not include path traversal or separators."""

    base_image = tmp_path / "base.img"
    base_image.write_bytes(b"raspberrypi")

    with pytest.raises(ImagerBuildError, match="Artifact name must start"):
        build_rpi4b_image(
            name="../outside",
            base_image_uri=str(base_image),
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=False,
        )


@pytest.mark.django_db
@patch("apps.imager.services.urlopen")
def test_build_rpi4b_image_downloads_percent_encoded_http_source(mock_urlopen, tmp_path: Path) -> None:
    """Regression: encoded HTTP paths should download and produce a valid artifact."""

    source_bytes = b"http-image"
    mock_urlopen.return_value = nullcontext(BytesIO(source_bytes))

    with patch("apps.imager.services._customize_image"):
        result = build_rpi4b_image(
            name="httpstable",
            base_image_uri="https://example.com/Raspberry%20Pi%20OS.img",
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=True,
        )

    assert result.output_path.exists()
    assert result.output_path.read_bytes() == source_bytes


@pytest.mark.django_db
def test_build_rpi4b_image_rejects_same_source_and_output_path(tmp_path: Path) -> None:
    """Regression: build should fail when source image equals output path."""

    output_path = tmp_path / "stable-rpi-4b.img"
    output_path.write_bytes(b"raspberrypi")

    with pytest.raises(ImagerBuildError, match="must differ from output artifact path"):
        build_rpi4b_image(
            name="stable",
            base_image_uri=str(output_path),
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=False,
        )


@pytest.mark.django_db
def test_build_rpi4b_image_treats_windows_drive_paths_as_local_sources(tmp_path: Path) -> None:
    """Regression: Windows drive-letter paths should fail as missing local files, not invalid schemes."""

    with pytest.raises(ImagerBuildError, match="Base image does not exist:"):
        build_rpi4b_image(
            name="stable",
            base_image_uri="C:/missing/base.img",
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=False,
        )


@pytest.mark.django_db
def test_build_rpi4b_image_treats_windows_backslash_drive_paths_as_local_sources(tmp_path: Path) -> None:
    """Regression: Windows drive-letter paths with backslashes should be treated as local paths."""

    with pytest.raises(ImagerBuildError, match="Base image does not exist:"):
        build_rpi4b_image(
            name="stable",
            base_image_uri=r"C:\\missing\\base.img",
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=False,
        )
