"""Regression tests for SMB Wikipedia defaults."""

from apps.app.models import DEFAULT_MODEL_WIKI_URLS



def test_smb_server_wikipedia_link_regression() -> None:
    """Regression: SMB server models should point to the canonical SMB article."""

    assert DEFAULT_MODEL_WIKI_URLS[("smb", "smb.SMBServer")] == (
        "https://en.wikipedia.org/wiki/Server_Message_Block"
    )
