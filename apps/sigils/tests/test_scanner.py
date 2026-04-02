from apps.sigils import scanner

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
