import json
import zipfile
from io import BytesIO

from scripts.ocpp_spec_refresh import (
    discover_download_url,
    extract_request_actions,
    identify_schema,
)


def test_discover_download_url_matches_exact_oca_label() -> None:
    html = """
    <html><body>
      <a href="/download/old">OCPP 2.0.1 Edition 3 (all files &amp; errata)</a>
      <a href="/download/current">OCPP 2.0.1 Edition 4 (all files)</a>
    </body></html>
    """

    assert (
        discover_download_url(
            html,
            label="OCPP 2.0.1 Edition 4 (all files)",
            base_url="https://openchargealliance.org/my-oca/ocpp/",
        )
        == "https://openchargealliance.org/download/current"
    )


def test_extract_request_actions_uses_schema_titles_and_filenames() -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(
            "schemas/AuthorizeRequest.json",
            json.dumps({"title": "AuthorizeRequest", "type": "object"}),
        )
        bundle.writestr(
            "schemas/BootNotification.json",
            json.dumps({"title": "BootNotificationRequest", "type": "object"}),
        )
        bundle.writestr(
            "schemas/HeartbeatRequest.json",
            json.dumps({"type": "object"}),
        )
        bundle.writestr(
            "schemas/AuthorizeResponse.json",
            json.dumps({"title": "AuthorizeResponse", "type": "object"}),
        )

    assert extract_request_actions(buffer.getvalue()) == {
        "Authorize",
        "BootNotification",
        "Heartbeat",
    }


def _zip_bytes(files: dict[str, bytes | str]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
    return buffer.getvalue()


def test_extract_request_actions_recurses_into_nested_zip() -> None:
    nested = _zip_bytes(
        {
            "schemas/AuthorizeRequest.json": json.dumps(
                {"title": "AuthorizeRequest", "type": "object"}
            ),
            "schemas/HeartbeatRequest.json": json.dumps({"type": "object"}),
        }
    )
    outer = _zip_bytes(
        {
            "OCPP-2.0.1-Part3-Schemas.zip": nested,
            "README.txt": "publication bundle",
        }
    )

    assert extract_request_actions(outer) == {"Authorize", "Heartbeat"}


def test_extract_request_actions_rejects_excessive_archive_nesting() -> None:
    payload = _zip_bytes(
        {
            "schemas/AuthorizeRequest.json": json.dumps(
                {"title": "AuthorizeRequest", "type": "object"}
            )
        }
    )
    for depth in range(6):
        payload = _zip_bytes({f"nested-{depth}.zip": payload})

    try:
        extract_request_actions(payload)
    except RuntimeError as error:
        assert "nesting exceeds" in str(error)
    else:
        raise AssertionError("excessively nested OCPP package should be rejected")


def test_identify_schema_prefers_title_over_filename() -> None:
    assert identify_schema(
        "package.zip!/schemas/ocpp16-message.json",
        {"title": "BootNotificationRequest", "type": "object"},
    ) == ("BootNotification", "request")


def test_identify_schema_falls_back_to_filename() -> None:
    assert identify_schema(
        "package.zip!/schemas/HeartbeatResponse.json",
        {"type": "object"},
    ) == ("Heartbeat", "response")
