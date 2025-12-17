from pathlib import Path

import pytest

from apps.nginx import services


@pytest.mark.parametrize("existing", [False, True])
def test_disable_nginx_management_toggles_lock(tmp_path: Path, existing: bool) -> None:
    lock_dir = tmp_path / services.LOCK_DIR_NAME
    lock_file = lock_dir / services.NGINX_DISABLED_LOCK

    if existing:
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_file.touch()

    services.disable_nginx_management(tmp_path)

    assert lock_file.exists()

    services.enable_nginx_management(tmp_path)

    assert not lock_file.exists()


def test_nginx_disabled_reports_state(tmp_path: Path) -> None:
    assert not services.nginx_disabled(tmp_path)

    services.disable_nginx_management(tmp_path)

    assert services.nginx_disabled(tmp_path)
