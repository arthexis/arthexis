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


def test_llvm_backend_fallbacks_to_python_when_library_is_missing(monkeypatch):
    scanner.get_scanner.cache_clear()
    monkeypatch.setenv("SIGIL_SCANNER_BACKEND", "llvm")
    monkeypatch.setenv("SIGIL_LLVM_LIBRARY", "/tmp/not-a-real-library.so")

    selected = scanner.get_scanner()

    assert isinstance(selected, scanner._PythonScanner)


def test_scan_sigil_tokens_uses_active_backend(monkeypatch):
    class StubScanner:
        def scan(self, text):
            return [scanner.TokenSpan(start=1, end=4)]

    scanner.get_scanner.cache_clear()
    monkeypatch.setattr(scanner, "get_scanner", lambda: StubScanner())

    spans = scanner.scan_sigil_tokens("abc")

    assert [(span.start, span.end) for span in spans] == [(1, 4)]


def test_resolve_sigils_consumes_scanner_spans(monkeypatch):
    from apps.sigils import sigil_resolver

    monkeypatch.setattr(
        sigil_resolver,
        "scan_sigil_tokens",
        lambda text: [scanner.TokenSpan(start=0, end=11)],
    )
    monkeypatch.setattr(sigil_resolver, "_resolve_token", lambda token, current=None: "ok")

    resolved = sigil_resolver.resolve_sigils("[ENV.VALUE]")

    assert resolved == "ok"



def test_default_backend_prefers_llvm(monkeypatch):
    scanner.get_scanner.cache_clear()
    monkeypatch.delenv("SIGIL_SCANNER_BACKEND", raising=False)
    attempted: list[str] = []

    class StubLlvmScanner:
        def __init__(self, library_path):
            attempted.append(library_path)

    monkeypatch.setenv("SIGIL_LLVM_LIBRARY", "/tmp/not-a-real-library.so")
    monkeypatch.setattr(scanner, "_LlvmScanner", StubLlvmScanner)

    selected = scanner.get_scanner()

    assert attempted == ["/tmp/not-a-real-library.so"]
    assert isinstance(selected, StubLlvmScanner)


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
