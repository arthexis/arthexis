from pathlib import Path

from apps.core.system import lifecycle


def test_role_lock_is_persisted_in_checkout(tmp_path: Path) -> None:
    current = lifecycle.InstallationLayout(
        root=tmp_path / "arthexis",
        checkout=tmp_path / "arthexis" / "app",
    )
    current.checkout.mkdir(parents=True)

    lifecycle._persist_role("Watchtower", current)

    role_lock = current.checkout / ".locks" / "role.lck"
    assert role_lock.read_text(encoding="utf-8") == "Watchtower\n"
    assert lifecycle._current_role(current) == "Watchtower"
