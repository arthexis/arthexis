from apps.events import manifest


def test_manifest_publishes_name_and_description() -> None:
    assert manifest.NAME == "events"
    assert manifest.DESCRIPTION
