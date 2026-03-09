import pytest
from django.core.management import call_command


@pytest.mark.django_db
def test_preview_command_help_lists_expected_options(capsys):
    """The short preview command should expose the expected CLI contract."""

    with pytest.raises(SystemExit):
        call_command("preview", "--help")

    output = capsys.readouterr().out
    assert "--base-url" in output
    assert "--engine" in output
