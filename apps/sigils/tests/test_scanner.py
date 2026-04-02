import pytest

from apps.sigils import scanner


@pytest.mark.parametrize(
    "text, expected",
    [
        ("plain-text", []),
        ("[ENV.VALUE]", [(0, 11)]),
        ("hello [ENV.VALUE] world [USR.name]", [(6, 17), (24, 34)]),
        ("nested [ENV=[ENV.KEY]] done", [(7, 22)]),
        ("[ENV.VALUE", []),
    ],
)
def test_python_scanner_finds_expected_token_spans(text, expected):
    spans = scanner._PythonScanner.scan(text)

    assert [(span.start, span.end) for span in spans] == expected


def test_llvm_scanner_maps_utf8_byte_offsets_to_character_spans(monkeypatch):
    text = "olé [ENV.VALUE]"

    llvm_scanner = scanner._LlvmScanner.__new__(scanner._LlvmScanner)

    encoded = text.encode("utf-8")
    start = encoded.index(b"[")
    end = start + len(b"[ENV.VALUE]")

    def fake_scanner(_encoded, _encoded_len, out_pairs, _max_pairs):
        out_pairs[0] = start
        out_pairs[1] = end
        return 1

    llvm_scanner._scanner = fake_scanner

    spans = llvm_scanner.scan(text)

    assert [(span.start, span.end) for span in spans] == [(4, 15)]
