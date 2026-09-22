from pathlib import Path


def test_ready_recipe_uses_bounded_repeat_interval() -> None:
    recipe = Path("deploy/ready.rx").read_text(encoding="utf-8")

    assert "ready --local" in recipe
    assert "--until true" in recipe
    assert "--max 10" in recipe
    assert "--interval 1" in recipe


def test_watchtower_recipe_composes_readiness() -> None:
    recipe = Path("deploy/watchtower.rx").read_text(encoding="utf-8")

    assert "./ready.rx" in recipe
