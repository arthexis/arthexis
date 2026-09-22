import json
import zipfile
from io import BytesIO

from scripts.ocpp_spec_refresh import discover_download_url, extract_request_actions


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
