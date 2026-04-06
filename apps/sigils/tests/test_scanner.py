import pytest

from apps.sigils import scanner


@pytest.mark.parametrize(
    "text, expected",
    [
        ("hello [ENV.VALUE] world [USR.name]", [(6, 17), (24, 34)]),
    ],
)
def test_python_scanner_finds_expected_token_spans(text, expected):
    spans = scanner._PythonScanner.scan(text)

    assert [(span.start, span.end) for span in spans] == expected


def test_get_scanner_returns_python_scanner():
    scanner.get_scanner.cache_clear()

    assert isinstance(scanner.get_scanner(), scanner._PythonScanner)
