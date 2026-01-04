from pathlib import Path

from scripts.check_invisible_characters import (
    INVISIBLE_CHARACTERS,
    InvisibleCharacterFinding,
    find_invisible_characters,
    scan_file_for_invisible_characters,
)


ZERO_WIDTH_SPACE = "\u200b"


def test_detects_invisible_characters(tmp_path: Path) -> None:
    target_file = tmp_path / "example.py"
    target_file.write_text(f"safe{ZERO_WIDTH_SPACE}code", encoding="utf-8")

    findings = scan_file_for_invisible_characters(target_file)

    assert findings == [
        InvisibleCharacterFinding(
            path=target_file,
            line=1,
            column=5,
            character=ZERO_WIDTH_SPACE,
            description=INVISIBLE_CHARACTERS[ZERO_WIDTH_SPACE],
        )
    ]


def test_repository_scan_skips_binary_like_files(tmp_path: Path) -> None:
    text_file = tmp_path / "visible.txt"
    text_file.write_text("ordinary text", encoding="utf-8")
    binary_file = tmp_path / "image.png"
    binary_file.write_bytes(b"\x89PNG\r\n\x1a\n")

    findings = find_invisible_characters(tmp_path)

    assert findings == []
