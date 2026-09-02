from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANUAL = ROOT / "docs/development/install-lifecycle-scripts-manual.md"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _read_shell_contract(path: str) -> str:
    return "\n".join(
        line for line in _read(path).splitlines() if not line.lstrip().startswith("#")
    )


def _read_usage_block(path: str) -> str:
    script_text = _read(path)
    usage_match = re.search(
        r"^usage\(\)\s*\{\n(?P<body>.*?)^\}\n",
        script_text,
        re.MULTILINE | re.DOTALL,
    )
    assert usage_match, f"{path} is missing usage() block"
    return usage_match.group("body")


def _upgrade_existing_lock_direct_include_python() -> str:
    upgrade_script = _read("upgrade.sh")
    match = re.search(
        r"existing_enabled_apps_lock_direct_includes\(\) \{.*?"
        r'"\$PYTHON_BIN" - "\$base_dir" "\$node_role" <<\'PY\'\n'
        r"(?P<body>.*?)\nPY\n\}",
        upgrade_script,
        re.MULTILINE | re.DOTALL,
    )
    assert match, "upgrade.sh is missing existing lock direct include helper"
    return match.group("body")


def _run_upgrade_existing_lock_direct_include_probe(base_dir: Path) -> set[str]:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _upgrade_existing_lock_direct_include_python(),
            str(base_dir),
            "Terminal",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {line for line in result.stdout.splitlines() if line.strip()}


def test_lifecycle_manual_covers_operator_entrypoints() -> None:
    manual = MANUAL.read_text(encoding="utf-8")

    expected_sections = (
        "## 1. Installation (`install.sh`)",
        "## 2.1 Startup (`start.sh`)",
        "## 2.2 Shutdown (`stop.sh`)",
        "## 3. Upgrades (`upgrade.sh`)",
        "## 4. Runtime reconfiguration (`configure.sh`)",
        "## 5. Runtime status (`status.sh`)",
        "## 6. Operational command entrypoint (`command.sh`)",
        "## 7. Uninstall (`uninstall.sh`)",
        "## 8. Error report (`error-report.sh`)",
    )

    for section in expected_sections:
        assert section in manual, f"Missing manual section: {section}"

    assert "flowchart TD" in manual
    assert "install.sh" in manual and "status.sh" in manual and "command.sh" in manual


def test_install_usage_keeps_core_lifecycle_flags() -> None:
    install_usage = _read_usage_block("install.sh")
    expected_flags = (
        "--service",
        "--port",
        "--upgrade",
        "--clean",
        "--repair",
        "--start",
        "--no-start",
        "--no-celery",
        "--satellite",
        "--terminal",
        "--control",
        "--watchtower",
        "--charger-facing",
        "--ocpp-gateway",
        "--no-charger-facing",
        "--imager-burner-service",
        "--no-imager-burner-service",
    )

    for flag in expected_flags:
        assert re.search(
            rf"(?<![\w-]){re.escape(flag)}(?![\w-])", install_usage
        ), f"install.sh usage is missing lifecycle flag: {flag}"


def test_install_debian_default_requires_redis_for_terminal_celery() -> None:
    install_script = _read_shell_contract("install.sh")
    default_match = re.search(
        r"if \[ \$\{#ORIGINAL_ARGS\[@\]\} -eq 0 \] && is_debian_host; then\n"
        r"(?P<body>.*?)\nfi",
        install_script,
        re.DOTALL,
    )
    assert default_match, "install.sh is missing Debian no-args default branch"

    body = default_match.group("body")
    assert 'SERVICE="arthexis"' in body
    assert "ENABLE_CELERY=true" in body
    assert 'NODE_ROLE="Terminal"' not in body
    assert "REQUIRES_REDIS=true" in body


def test_install_supports_explicit_no_celery_override() -> None:
    install_script = _read_shell_contract("install.sh")

    assert 'CELERY_MODE=""' in install_script
    assert "--celery|--no-celery" in _read_usage_block("install.sh")
    assert "--no-celery)" in install_script
    assert "Cannot combine --celery with --no-celery" in install_script
    assert 'CELERY_MODE="disable"' in install_script
    assert (
        'elif [ "$CELERY_MODE" = "disable" ]; then\n'
        "    ENABLE_CELERY=false" in install_script
    )
    assert (
        '[ "$ENABLE_CELERY" = false ] && [ "$CELERY_MODE" != "disable" ] '
        '&& [ -f "$LOCK_DIR_PATH/celery.lck" ]' in install_script
    )
    assert (
        '[ "$ENABLE_CELERY" = false ] && '
        '[ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]'
        in install_script
    )
    assert 'arthexis_remove_celery_unit_stack "$LOCK_DIR" "$SERVICE"' in install_script


def test_install_reexec_preserves_explicit_migration_policy() -> None:
    install_script = _read_shell_contract("install.sh")

    assert 'ARTHEXIS_RUN_AS_USER="$TARGET_USER"' in install_script
    assert 'ARTHEXIS_MIGRATION_POLICY="${ARTHEXIS_MIGRATION_POLICY:-}"' in install_script


def test_install_no_start_and_embedded_skip_systemd_restart() -> None:
    install_script = _read_shell_contract("install.sh")
    service_helper = _read_shell_contract("scripts/helpers/systemd_locks.sh")
    restart_guard = (
        'elif [ -n "$SERVICE" ] && [ "$START_FLAG" = false ] && '
        '[ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then'
    )
    helper_start = service_helper.index("arthexis_install_service_stack()")
    helper_body = service_helper[helper_start:]

    assert restart_guard in install_script
    assert 'sudo systemctl restart "$SERVICE"' in install_script
    assert install_script.index(restart_guard) < install_script.index(
        'sudo systemctl restart "$SERVICE"'
    )
    assert (
        'if [ "$service_mode" != "systemd" ]; then\n    return 0\n  fi' in helper_body
    )
    assert helper_body.index(
        'if [ "$service_mode" != "systemd" ]; then'
    ) < helper_body.index("sudo bash -c")
    for guarded_unit in (
        '"${LCD_SERVICE}.service"',
        '"${RFID_SERVICE}.service"',
        '"${CAMERA_SERVICE}.service"',
        '"$ARTHEXIS_IMAGER_BURNER_SERVICE_UNIT"',
        '"${SERVICE}-boot-upgrade.service"',
    ):
        assert (
            f'if arthexis_systemd_unit_recorded "$LOCK_DIR" {guarded_unit}; then'
            in install_script
        )


def test_rfid_systemd_unit_uses_django_management_entrypoint() -> None:
    service_helper = _read_shell_contract("scripts/helpers/systemd_locks.sh")
    rfid_start = service_helper.index("arthexis_install_rfid_service_unit()")
    rfid_end = service_helper.index("arthexis_install_camera_service_unit()")
    rfid_body = service_helper[rfid_start:rfid_end]

    assert "ExecStart=$base_dir/.venv/bin/python manage.py rfid service" in rfid_body
    assert "-m apps.cards.rfid_service" not in rfid_body


def test_boot_upgrade_service_stack_has_no_extra_layout_cleanup_unit() -> None:
    service_helper = _read_shell_contract("scripts/helpers/systemd_locks.sh")
    boot_unit_start = service_helper.index(
        "arthexis_install_boot_upgrade_service_unit()"
    )
    boot_unit_end = service_helper.index(
        "arthexis_update_systemd_service_user()",
        boot_unit_start,
    )
    boot_unit_body = service_helper[boot_unit_start:boot_unit_end]
    service_user_update_start = service_helper.index(
        "arthexis_update_systemd_service_user()"
    )
    service_user_update_body = service_helper[service_user_update_start:]

    assert "Before=" not in boot_unit_body
    assert "Wants=network-online.target" in boot_unit_body
    assert "Requires=${service_name}-boot-upgrade.service" in service_helper
    assert "After=${service_name}-boot-upgrade.service" in service_helper
    assert "User=$service_user" in boot_unit_body
    assert "*-boot-upgrade.service)" not in service_user_update_body


def test_systemd_user_update_purges_retired_kiosk_cleanup_records() -> None:
    service_helper = _read("scripts/helpers/systemd_locks.sh")
    assert (
        "*-kiosk-layout-cleanup.service|*-kiosk-layout-cleanup.path)" in service_helper
    )
    assert (
        'arthexis_remove_systemd_unit_if_present "$lock_dir" "$unit"' in service_helper
    )


def test_lifecycle_scripts_do_not_retain_retired_feature_cleanup_hooks() -> None:
    retired_tokens = (
        "manage.py audio_alert shutdown",
        "ocpp_simulator",
        "simulator.json",
        "playwright install",
        "ensure_playwright",
    )
    lifecycle_scripts = (
        "install.sh",
        "upgrade.sh",
        "env-refresh.sh",
        "start.sh",
        "stop.sh",
        "status.sh",
        "configure.sh",
        "uninstall.sh",
        "scripts/helpers/systemd_locks.sh",
    )

    for script in lifecycle_scripts:
        script_text = _read_shell_contract(script)
        for token in retired_tokens:
            assert token not in script_text, f"{script} still references {token}"


def test_upgrade_retires_legacy_kiosk_systemd_units() -> None:
    upgrade_script = _read_shell_contract("upgrade.sh")
    assert "retire_legacy_kiosk_units()" in upgrade_script
    assert (
        "if [[ $CHECK_ONLY -ne 1 ]]; then\n  retire_legacy_kiosk_units\nfi"
        in upgrade_script
    )
    assert '"arthexis-hdmi-kiosk.service"' in upgrade_script
    assert '"arthexis-hdmi-kiosk-layout.service"' in upgrade_script
    assert '"arthexis-hdmi-kiosk-layout.path"' in upgrade_script
    assert (
        "*-kiosk-layout-cleanup.service|*-kiosk-layout-cleanup.path)" in upgrade_script
    )
    assert "local legacy_units_to_remove=()" in upgrade_script
    assert 'legacy_units_to_remove+=("$legacy_unit")' in upgrade_script
    assert 'for legacy_unit in "${legacy_units_to_remove[@]}"; do' in upgrade_script


def test_dev_env_exposes_only_native_modes() -> None:
    script = _read("dev-env.sh")
    _, mode_dispatch = script.split('case "$MODE" in', 1)
    auto_case = re.search(
        r"auto\)\n(?P<body>.*?)\n    ;;\n  local\)",
        mode_dispatch,
        re.DOTALL,
    )
    assert auto_case, "dev-env.sh is missing auto mode"

    auto_body = auto_case.group("body")
    removed_runtime = "doc" "ker"
    removed_helper = "run_" "container_path"
    removed_flag = "--" "container"
    removed_case = "container" ")"
    assert "run_local_path" in auto_body
    assert removed_helper not in auto_body
    assert removed_flag not in script
    assert removed_case not in script
    assert removed_runtime not in script.lower()


def test_install_script_supports_explicit_charger_facing_lock() -> None:
    install_script = _read_shell_contract("install.sh")

    assert "--charger-facing)" in install_script
    assert "--ocpp-gateway)" in install_script
    assert "--no-charger-facing|--no-ocpp-gateway)" in install_script
    assert 'CHARGER_FACING_LOCK="$LOCK_DIR/charger_facing.lck"' in install_script
    assert 'OCPP_GATEWAY_LOCK="$LOCK_DIR/ocpp_gateway.lck"' in install_script
    assert "CHARGER_FACING_ROUTE_EXPLICIT=false" in install_script
    assert (
        'CHARGER_FACING_DISABLED_LOCK="$LOCK_DIR/charger_facing_disabled.lck"'
        in install_script
    )
    assert 'touch "$CHARGER_FACING_LOCK"' in install_script
    assert 'touch "$OCPP_GATEWAY_LOCK"' in install_script
    assert 'touch "$CHARGER_FACING_DISABLED_LOCK"' in install_script
    assert 'rm -f "$CHARGER_FACING_LOCK"' in install_script
    assert 'rm -f "$OCPP_GATEWAY_LOCK"' in install_script
    assert 'rm -f "$CHARGER_FACING_DISABLED_LOCK"' in install_script
    for flag in (
        "--charger-facing",
        "--ocpp-gateway",
        "--no-charger-facing|--no-ocpp-gateway",
    ):
        case_match = re.search(
            rf"{re.escape(flag)}\)\n(?P<body>.*?)\n\s*;;",
            install_script,
            re.DOTALL,
        )
        assert case_match, f"install.sh is missing parser case for {flag}"
        assert "CHARGER_FACING_ROUTE_EXPLICIT=true" in case_match.group("body")
    assert '[[ -f "$LOCK_DIR_PATH/charger_facing.lck" ]]' in install_script
    assert '[[ -f "$LOCK_DIR_PATH/ocpp_gateway.lck" ]]' in install_script
    assert (
        'if [[ "$CLEAN" == false && "$ENABLE_CHARGER_FACING" == false && "$ENABLE_OCPP_GATEWAY" == false && "$DISABLE_CHARGER_FACING" == false ]]; then\n    if [[ -f "$LOCK_DIR_PATH/charger_facing_disabled.lck" ]]; then'
        in install_script
    )


def test_install_role_defaults_make_satellite_and_control_charger_facing() -> None:
    install_script = _read_shell_contract("install.sh")

    assert "apply_role_charger_facing_default()" in install_script
    assert 'case "${NODE_ROLE,,}" in' in install_script
    assert "satellite|control)" in install_script
    assert (
        '[[ "$ENABLE_CHARGER_FACING" == false && "$ENABLE_OCPP_GATEWAY" == false && '
        '"$DISABLE_CHARGER_FACING" == false ]]' in install_script
    )
    assert "ENABLE_CHARGER_FACING=true" in install_script
    assert "apply_role_charger_facing_default" in install_script


def test_install_writes_role_enabled_apps_lock_before_env_refresh() -> None:
    install_script = _read_shell_contract("install.sh")

    assert "write_role_enabled_apps_lock()" in install_script
    assert 'local mode="${2:-preserve}"' in install_script
    assert (
        "from utils.enabled_apps_lock import write_enabled_apps_lock" in install_script
    )
    for imported_name in (
        "explain_role_app_selectors",
        "get_direct_lock_app_selectors",
    ):
        assert imported_name in install_script
    assert "direct_apps=direct_selectors" in install_script
    assert "direct_app_sources=direct_sources" in install_script
    assert "def direct_result_source_for_reasons(reasons):" in install_script
    assert 'if "explicit-include" in reasons:\n        return None' in install_script
    assert "def direct_result_sources(result):" in install_script
    assert "direct_sources = direct_result_sources(result)" in install_script
    assert "def charger_facing_routes_enabled(base_dir):" in install_script
    assert '"charger_facing.lck"' in install_script
    assert '"ocpp_gateway.lck"' in install_script
    assert 'direct_selectors = (*direct_selectors, "apps.ocpp")' in install_script
    assert 'if [ -f "$lock_path" ] && [ "$mode" != "refresh" ]; then' in install_script
    assert "refresh_charger_facing_route_lock_metadata()" in install_script
    assert "role_app_profiles_explicitly_enabled()" in install_script
    assert "role_app_profile_inputs_present()" in install_script
    assert "charger_facing_route_refresh_required()" in install_script
    assert (
        '[[ "$CHARGER_FACING_ROUTE_EXPLICIT" == true ]] && return 0' in install_script
    )
    assert (
        '[[ "$ENABLE_CHARGER_FACING" == true || "$ENABLE_OCPP_GATEWAY" == true ]]'
        in install_script
    )
    assert "ARTHEXIS_ROLE_APP_FEATURE_PACKS" in install_script
    assert "ARTHEXIS_FEATURE_PACKS" in install_script
    assert "ARTHEXIS_ROLE_APP_DISABLED_APPS" in install_script
    assert "ARTHEXIS_DISABLED_APPS" in install_script
    assert "PREVIOUS_NODE_ROLE=" in install_script
    assert (
        '[[ -f "$LOCK_DIR/enabled_apps.lck" && -n "$PREVIOUS_NODE_ROLE" && '
        '"${PREVIOUS_NODE_ROLE,,}" != "${NODE_ROLE,,}" ]]' in install_script
    )
    assert 'write_role_enabled_apps_lock "$NODE_ROLE" refresh' in install_script
    assert "elif role_app_profile_inputs_present; then" in install_script
    assert (
        'elif [ "$EXISTING_INSTALL" = false ] && [ "$NODE_ROLE_EXPLICIT" = true ]; then'
        in install_script
    )
    assert (
        'elif charger_facing_route_refresh_required && [[ -f "$LOCK_DIR/enabled_apps.lck" ]]; then'
        in install_script
    )
    assert "elif role_app_profiles_explicitly_enabled; then" in install_script
    assert "refresh_charger_facing_route_lock_metadata" in install_script
    assert (
        "Install without explicit role app profile opt-in; preserving full app fallback."
        in install_script
    )
    assert 'write_role_enabled_apps_lock "$NODE_ROLE"' in install_script
    assert (
        "No enabled-apps lock present; preserving full app fallback" in install_script
    )

    assert (
        "refresh_charger_facing_route_lock_metadata()" in install_script
    ), "install.sh is missing route metadata refresh function"
    metadata_body = install_script.split(
        "refresh_charger_facing_route_lock_metadata()", 1
    )[1].split("role_app_profiles_explicitly_enabled()", 1)[0]
    assert "read_enabled_apps_lock(base_dir)" in metadata_body
    assert "read_enabled_apps_lock_direct_entries(base_dir)" in metadata_body
    assert "read_enabled_apps_lock_direct_sources(base_dir)" in metadata_body
    assert 'direct_sources.get("apps.ocpp") == "charger-facing"' in metadata_body
    assert "write_enabled_apps_lock(\n    enabled_entries," in metadata_body
    assert "explain_role_app_selectors" not in metadata_body

    previous_role_index = install_script.index("PREVIOUS_NODE_ROLE=")
    role_lock_index = install_script.index('echo "$NODE_ROLE" > "$LOCK_DIR/role.lck"')
    existing_install_index = install_script.index("EXISTING_INSTALL=false")
    lock_write_index = install_script.index('write_role_enabled_apps_lock "$NODE_ROLE"')
    charger_facing_refresh_index = install_script.index(
        'elif charger_facing_route_refresh_required && [[ -f "$LOCK_DIR/enabled_apps.lck" ]]; then'
    )
    role_profiles_index = install_script.index(
        "elif role_app_profiles_explicitly_enabled; then"
    )
    env_refresh_index = install_script.index('run_env_refresh "${env_refresh_args[@]}"')
    assert (
        previous_role_index
        < role_lock_index
        < existing_install_index
        < lock_write_index
        < charger_facing_refresh_index
        < role_profiles_index
        < env_refresh_index
    )


def test_install_repair_preserves_locked_node_role(tmp_path: Path) -> None:
    bash = _find_usable_bash()
    if bash is None:
        pytest.skip("bash is required for install.sh lifecycle contract tests")

    script_text = _read("install.sh")
    function_bodies = []
    for function_name in (
        "apply_node_role_runtime_defaults",
        "restore_node_role_from_lock_for_repair",
    ):
        function_match = re.search(
            rf"^{function_name}\(\)\s*\{{\n(?P<body>.*?)^\}}\n",
            script_text,
            re.MULTILINE | re.DOTALL,
        )
        assert function_match, f"install.sh is missing {function_name}()"
        function_bodies.append(
            f"{function_name}() {{\n{function_match.group('body')}}}"
        )

    harness = tmp_path / "restore-role.sh"
    functions_text = "\n\n".join(function_bodies)
    _write_executable(
        harness,
        f"""
        #!/usr/bin/env bash
        set -euo pipefail
        NODE_ROLE="Terminal"
        NODE_ROLE_EXPLICIT="$1"
        REQUIRES_REDIS=false
        ENABLE_CONTROL=false
        {functions_text}
        restore_node_role_from_lock_for_repair "$2"
        printf '%s|%s|%s\\n' "$NODE_ROLE" "$REQUIRES_REDIS" "$ENABLE_CONTROL"
        """,
    )

    def run_case(role_text: str, *, explicit: bool = False) -> str:
        role_lock = (
            tmp_path / f"role-{role_text.strip().lower() or 'empty'}-{explicit}.lck"
        )
        role_lock.write_text(role_text, encoding="utf-8")
        result = subprocess.run(
            [bash, str(harness), str(explicit).lower(), str(role_lock)],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.strip()

    assert run_case("Satellite\n") == "Satellite|true|false"
    assert run_case("Control\n") == "Control|true|true"
    assert run_case("Watchtower\n") == "Watchtower|true|false"
    assert run_case("Terminal\n") == "Terminal|false|false"
    assert run_case("Satellite\n", explicit=True) == "Terminal|false|false"


def test_standalone_systemd_units_load_arthexis_env() -> None:
    systemd_helper = _read_shell_contract("scripts/helpers/systemd_locks.sh")
    suite_units = [
        body
        for body in re.findall(
            r"<<SERVICEEOF\n(.*?)\nSERVICEEOF", systemd_helper, re.DOTALL
        )
        if "Description=Arthexis Constellation Django service" in body
    ]
    imager_burner_units = [
        body
        for body in re.findall(
            r"<<SERVICEEOF\n(.*?)\nSERVICEEOF", systemd_helper, re.DOTALL
        )
        if "Description=Durable SD-card burner worker for Arthexis" in body
    ]
    unit_bodies = (
        suite_units
        + [
            re.search(
                rf"<<{marker}\n(?P<body>.*?)\n{marker}", systemd_helper, re.DOTALL
            ).group("body")
            for marker in ("CELERYSERVICEEOF", "BEATSERVICEEOF")
        ]
        + imager_burner_units
    )
    assert len(unit_bodies) == 4
    for unit_body in unit_bodies:
        assert "EnvironmentFile=-$base_dir/arthexis.env" in unit_body
        assert "EnvironmentFile=-$base_dir/redis.env" in unit_body
        assert "EnvironmentFile=-$base_dir/debug.env" in unit_body


def test_lifecycle_scripts_support_imager_burner_service_unit() -> None:
    install_script = _read_shell_contract("install.sh")
    configure_script = _read_shell_contract("configure.sh")
    uninstall_script = _read_shell_contract("uninstall.sh")
    upgrade_script = _read_shell_contract("upgrade.sh")
    service_manager = _read_shell_contract("scripts/helpers/service_manager.sh")
    systemd_helper = _read_shell_contract("scripts/helpers/systemd_locks.sh")

    assert "--imager-burner-service)" in install_script
    assert "--no-imager-burner-service)" in install_script
    assert (
        'arthexis_install_imager_burner_service_unit "$BASE_DIR" "$LOCK_DIR" "$SERVICE"'
        in install_script
    )
    assert "--imager-burner-service)" in configure_script
    assert "--no-imager-burner-service)" in configure_script
    assert "apply_imager_burner_service_setting" in configure_script
    assert (
        'arthexis_install_imager_burner_service_unit "$BASE_DIR" "$LOCK_DIR" "$SERVICE"'
        in configure_script
    )
    assert "command -v lsblk >/dev/null 2>&1" in install_script
    assert "command -v lsblk >/dev/null 2>&1" in configure_script
    assert "ARTHEXIS_IMAGER_BURNER_SERVICE_UNIT" in service_manager
    assert "arthexis_install_imager_burner_service_unit()" in systemd_helper
    assert "arthexis_install_control_usb_stability_rules()" in systemd_helper
    assert "arthexis_install_control_usb_polling_timer_overrides()" in systemd_helper
    assert "arthexis_remove_control_usb_polling_timer_overrides()" in systemd_helper
    assert (
        'arthexis_install_control_usb_polling_timer_overrides "$LOCK_DIR"'
        in install_script
    )
    assert (
        'arthexis_install_control_usb_polling_timer_overrides "$LOCK_DIR"'
        in configure_script
    )
    assert (
        'arthexis_install_control_usb_polling_timer_overrides "$LOCK_DIR"'
        in upgrade_script
    )
    assert (
        'arthexis_install_control_usb_polling_timer_overrides "$LOCK_DIR" true'
        in upgrade_script
    )
    assert "local preserve_existing" in systemd_helper
    assert "arthexis_remove_control_usb_polling_timer_overrides" in install_script
    assert "arthexis_remove_control_usb_polling_timer_overrides" in configure_script
    assert "arthexis_remove_control_usb_polling_timer_overrides" in uninstall_script
    assert "arthexis_remove_control_usb_polling_timer_overrides" in upgrade_script
    assert '"${NODE_ROLE_NAME,,}" == "control"' in upgrade_script
    assert "92-arthexis-control-usb-wifi-power.rules" in systemd_helper
    assert "93-arthexis-imager-burner-ignore.rules" in systemd_helper
    assert "IMAGER_GWAY_BURN_DEVICE" in systemd_helper
    assert 'arthexis_install_control_usb_stability_rules "$base_dir"' in systemd_helper
    imager_burner_unit = re.search(
        r"<<SERVICEEOF\n(?P<body>.*?)\nSERVICEEOF",
        systemd_helper[
            systemd_helper.index("Description=Durable SD-card burner worker") - 100 :
        ],
        re.DOTALL,
    ).group("body")
    assert "After=${service_name}.service network-online.target" in imager_burner_unit
    assert "Wants=${service_name}.service" in imager_burner_unit
    assert "PartOf=${service_name}.service" not in imager_burner_unit
    assert "--no-quiet-usb" in _read_shell_contract(
        "apps/imager/management/commands/imager.py"
    )


def test_systemd_locks_installs_control_usb_polling_timer_overrides(
    tmp_path: Path,
) -> None:
    bash = _find_usable_bash()
    if bash is None:
        pytest.skip("bash is required for systemd_locks.sh lifecycle contract tests")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    command_log = tmp_path / "commands.log"
    systemd_dir = tmp_path / "systemd"
    lock_dir = tmp_path / "locks"

    sudo = bin_dir / "sudo"
    sudo.write_text(
        "#!/bin/sh\n"
        'printf "sudo %s\\n" "$*" >> "$COMMAND_LOG"\n'
        'if [ "$1" = install ]; then\n'
        "  shift\n"
        '  exec install "$@"\n'
        "fi\n"
        'if [ "$1" = tee ]; then\n'
        "  shift\n"
        '  exec tee "$@"\n'
        "fi\n"
        'if [ "$1" = chmod ]; then\n'
        "  shift\n"
        '  exec chmod "$@"\n'
        "fi\n"
        'if [ "$1" = systemctl ]; then\n'
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    sudo.chmod(0o755)
    systemctl = bin_dir / "systemctl"
    systemctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    systemctl.chmod(0o755)

    env = os.environ.copy()
    env["COMMAND_LOG"] = str(command_log)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["SYSTEMD_DIR"] = str(systemd_dir)
    env["ARTHEXIS_USB_INVENTORY_TIMER_ON_UNIT_ACTIVE_SEC"] = "7min"

    result = subprocess.run(
        [
            bash,
            "-c",
            'source "$1"; arthexis_install_control_usb_polling_timer_overrides "$2"',
            "bash",
            str(ROOT / "scripts/helpers/systemd_locks.sh"),
            str(lock_dir),
        ],
        check=True,
        capture_output=True,
        env=env,
        text=True,
        timeout=10,
    )

    assert result.stdout == ""
    inventory_override = (
        systemd_dir
        / "arthexis-usb-inventory.timer.d"
        / "10-arthexis-control-usb-polling.conf"
    ).read_text(encoding="utf-8")
    bastion_override = (
        systemd_dir
        / "bastion-usb-refresh.timer.d"
        / "10-arthexis-control-usb-polling.conf"
    ).read_text(encoding="utf-8")
    assert "OnBootSec=\nOnUnitActiveSec=\nRandomizedDelaySec=" in inventory_override
    assert "OnBootSec=2min" in inventory_override
    assert "OnUnitActiveSec=7min" in inventory_override
    assert "RandomizedDelaySec=30s" in inventory_override
    assert "OnBootSec=3min" in bastion_override
    assert "OnUnitActiveSec=10min" in bastion_override
    assert "RandomizedDelaySec=60s" in bastion_override
    assert not (lock_dir / "systemd_services.lck").exists()
    commands = command_log.read_text(encoding="utf-8")
    assert (
        "sudo chmod 0644 "
        f"{systemd_dir}/arthexis-usb-inventory.timer.d/"
        "10-arthexis-control-usb-polling.conf" in commands
    )
    assert (
        "sudo chmod 0644 "
        f"{systemd_dir}/bastion-usb-refresh.timer.d/"
        "10-arthexis-control-usb-polling.conf" in commands
    )
    assert "sudo systemctl daemon-reload" in commands
    assert (
        "sudo systemctl try-restart arthexis-usb-inventory.timer "
        "bastion-usb-refresh.timer" in commands
    )


def test_systemd_locks_preserves_existing_control_usb_polling_timer_overrides(
    tmp_path: Path,
) -> None:
    bash = _find_usable_bash()
    if bash is None:
        pytest.skip("bash is required for systemd_locks.sh lifecycle contract tests")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    command_log = tmp_path / "commands.log"
    systemd_dir = tmp_path / "systemd"
    lock_dir = tmp_path / "locks"
    inventory_dropin = systemd_dir / "arthexis-usb-inventory.timer.d"
    inventory_dropin.mkdir(parents=True)
    inventory_override = inventory_dropin / "10-arthexis-control-usb-polling.conf"
    inventory_override.write_text(
        "[Timer]\nOnUnitActiveSec=17min\n",
        encoding="utf-8",
    )

    sudo = bin_dir / "sudo"
    sudo.write_text(
        "#!/bin/sh\n"
        'printf "sudo %s\\n" "$*" >> "$COMMAND_LOG"\n'
        'if [ "$1" = install ]; then\n'
        "  shift\n"
        '  exec install "$@"\n'
        "fi\n"
        'if [ "$1" = tee ]; then\n'
        "  shift\n"
        '  exec tee "$@"\n'
        "fi\n"
        'if [ "$1" = chmod ]; then\n'
        "  shift\n"
        '  exec chmod "$@"\n'
        "fi\n"
        'if [ "$1" = systemctl ]; then\n'
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    sudo.chmod(0o755)
    systemctl = bin_dir / "systemctl"
    systemctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    systemctl.chmod(0o755)

    env = os.environ.copy()
    env["COMMAND_LOG"] = str(command_log)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["SYSTEMD_DIR"] = str(systemd_dir)

    result = subprocess.run(
        [
            bash,
            "-c",
            'source "$1"; arthexis_install_control_usb_polling_timer_overrides "$2" true',
            "bash",
            str(ROOT / "scripts/helpers/systemd_locks.sh"),
            str(lock_dir),
        ],
        check=True,
        capture_output=True,
        env=env,
        text=True,
        timeout=10,
    )

    assert result.stdout == ""
    assert inventory_override.read_text(encoding="utf-8") == (
        "[Timer]\nOnUnitActiveSec=17min\n"
    )
    bastion_override = (
        systemd_dir
        / "bastion-usb-refresh.timer.d"
        / "10-arthexis-control-usb-polling.conf"
    )
    assert "OnUnitActiveSec=10min" in bastion_override.read_text(encoding="utf-8")
    commands = command_log.read_text(encoding="utf-8")
    assert f"sudo tee {inventory_override}" not in commands
    assert f"sudo chmod 0644 {inventory_override}" not in commands
    assert f"sudo tee {bastion_override}" in commands


def test_systemd_locks_removes_control_usb_polling_timer_overrides(
    tmp_path: Path,
) -> None:
    bash = _find_usable_bash()
    if bash is None:
        pytest.skip("bash is required for systemd_locks.sh lifecycle contract tests")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    command_log = tmp_path / "commands.log"
    systemd_dir = tmp_path / "systemd"
    for unit_name in ("arthexis-usb-inventory.timer", "bastion-usb-refresh.timer"):
        dropin_dir = systemd_dir / f"{unit_name}.d"
        dropin_dir.mkdir(parents=True)
        (dropin_dir / "10-arthexis-control-usb-polling.conf").write_text(
            "[Timer]\n",
            encoding="utf-8",
        )
        (dropin_dir / "override.conf").write_text(
            "# operator-owned override\n",
            encoding="utf-8",
        )

    sudo = bin_dir / "sudo"
    sudo.write_text(
        "#!/bin/sh\n"
        'printf "sudo %s\\n" "$*" >> "$COMMAND_LOG"\n'
        'if [ "$1" = rm ]; then\n'
        "  shift\n"
        '  exec rm "$@"\n'
        "fi\n"
        'if [ "$1" = rmdir ]; then\n'
        "  shift\n"
        '  exec rmdir "$@"\n'
        "fi\n"
        'if [ "$1" = systemctl ]; then\n'
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    sudo.chmod(0o755)
    systemctl = bin_dir / "systemctl"
    systemctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    systemctl.chmod(0o755)

    env = os.environ.copy()
    env["COMMAND_LOG"] = str(command_log)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["SYSTEMD_DIR"] = str(systemd_dir)

    result = subprocess.run(
        [
            bash,
            "-c",
            'source "$1"; arthexis_remove_control_usb_polling_timer_overrides',
            "bash",
            str(ROOT / "scripts/helpers/systemd_locks.sh"),
        ],
        check=True,
        capture_output=True,
        env=env,
        text=True,
        timeout=10,
    )

    assert result.stdout == ""
    assert not (
        systemd_dir
        / "arthexis-usb-inventory.timer.d"
        / "10-arthexis-control-usb-polling.conf"
    ).exists()
    assert not (
        systemd_dir
        / "bastion-usb-refresh.timer.d"
        / "10-arthexis-control-usb-polling.conf"
    ).exists()
    assert (systemd_dir / "arthexis-usb-inventory.timer.d" / "override.conf").read_text(
        encoding="utf-8"
    ) == "# operator-owned override\n"
    assert (systemd_dir / "bastion-usb-refresh.timer.d" / "override.conf").read_text(
        encoding="utf-8"
    ) == "# operator-owned override\n"
    commands = command_log.read_text(encoding="utf-8")
    assert (
        "sudo rm -f "
        f"{systemd_dir}/arthexis-usb-inventory.timer.d/"
        "10-arthexis-control-usb-polling.conf" in commands
    )
    assert (
        "sudo rm -f "
        f"{systemd_dir}/bastion-usb-refresh.timer.d/"
        "10-arthexis-control-usb-polling.conf" in commands
    )
    assert "sudo systemctl daemon-reload" in commands
    assert (
        "sudo systemctl try-restart arthexis-usb-inventory.timer "
        "bastion-usb-refresh.timer" in commands
    )


def test_systemd_locks_skips_control_usb_polling_cleanup_when_dropin_absent(
    tmp_path: Path,
) -> None:
    bash = _find_usable_bash()
    if bash is None:
        pytest.skip("bash is required for systemd_locks.sh lifecycle contract tests")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    command_log = tmp_path / "commands.log"
    systemd_dir = tmp_path / "systemd"
    dropin_dir = systemd_dir / "arthexis-usb-inventory.timer.d"
    dropin_dir.mkdir(parents=True)
    (dropin_dir / "override.conf").write_text(
        "# operator-owned override\n",
        encoding="utf-8",
    )

    sudo = bin_dir / "sudo"
    sudo.write_text(
        "#!/bin/sh\n" 'printf "sudo %s\\n" "$*" >> "$COMMAND_LOG"\n' "exit 1\n",
        encoding="utf-8",
    )
    sudo.chmod(0o755)
    systemctl = bin_dir / "systemctl"
    systemctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    systemctl.chmod(0o755)

    env = os.environ.copy()
    env["COMMAND_LOG"] = str(command_log)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["SYSTEMD_DIR"] = str(systemd_dir)

    result = subprocess.run(
        [
            bash,
            "-c",
            'set -e; source "$1"; '
            "arthexis_remove_control_usb_polling_timer_overrides",
            "bash",
            str(ROOT / "scripts/helpers/systemd_locks.sh"),
        ],
        check=True,
        capture_output=True,
        env=env,
        text=True,
        timeout=10,
    )

    assert result.stdout == ""
    assert (dropin_dir / "override.conf").read_text(
        encoding="utf-8"
    ) == "# operator-owned override\n"
    assert not command_log.exists()


def test_systemd_locks_reads_imager_burner_env_value_before_inline_comment(
    tmp_path: Path,
) -> None:
    bash = _find_usable_bash()
    if bash is None:
        pytest.skip("bash is required for systemd_locks.sh lifecycle contract tests")

    (tmp_path / "arthexis.env").write_text(
        "IMAGER_GWAY_BURN_DEVICE=/dev/disk/by-id/usb-SanDisk_3.2Gen1 # burner\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            bash,
            "-c",
            'source "$1"; arthexis_configured_imager_burn_device "$2"',
            "bash",
            str(ROOT / "scripts/helpers/systemd_locks.sh"),
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.stdout.strip() == "/dev/disk/by-id/usb-SanDisk_3.2Gen1"


def test_systemd_locks_retriggers_configured_attached_burner(
    tmp_path: Path,
) -> None:
    bash = _find_usable_bash()
    if bash is None:
        pytest.skip("bash is required for systemd_locks.sh lifecycle contract tests")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    command_log = tmp_path / "commands.log"
    base_dir = tmp_path / "suite"
    base_dir.mkdir()
    (base_dir / "arthexis.env").write_text(
        "IMAGER_BURN_DEVICE=/dev/disk/by-id/usb-SD-Reader\n",
        encoding="utf-8",
    )
    sudo = bin_dir / "sudo"
    sudo.write_text(
        "#!/bin/sh\n"
        'printf "sudo %s\\n" "$*" >> "$COMMAND_LOG"\n'
        'if [ "$1" = tee ]; then\n'
        "  while IFS= read -r _line; do :; done\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    sudo.chmod(0o755)
    udevadm = bin_dir / "udevadm"
    udevadm.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    udevadm.chmod(0o755)
    readlink = bin_dir / "readlink"
    readlink.write_text(
        "#!/bin/sh\n" 'if [ "$1" = -f ]; then\n' "  printf '/dev/sdb\\n'\n" "fi\n",
        encoding="utf-8",
    )
    readlink.chmod(0o755)

    env = os.environ.copy()
    env["COMMAND_LOG"] = str(command_log)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["UDEV_RULES_DIR"] = str(tmp_path / "udev")
    result = subprocess.run(
        [
            bash,
            "-c",
            'source "$1"; arthexis_install_control_usb_stability_rules "$2"',
            "bash",
            str(ROOT / "scripts/helpers/systemd_locks.sh"),
            str(base_dir),
        ],
        check=True,
        capture_output=True,
        env=env,
        text=True,
        timeout=10,
    )

    assert result.stdout == ""
    commands = command_log.read_text(encoding="utf-8")
    assert "sudo udevadm control --reload-rules" in commands
    assert (
        "sudo udevadm trigger --action=change --subsystem-match=block "
        "--name-match=/dev/sdb" in commands
    )


def test_watchtower_connect_update_artifact_script_uses_native_imager() -> None:
    script = _read("scripts/watchtower-connect-update-artifact.sh")
    removed_runtime = "doc" "ker"

    assert removed_runtime not in script.lower()
    assert "ARTHEXIS_ROLE_APP_FEATURE_PACKS" in script
    assert "rpi_connect_updates" in script
    assert "apps.imager" in script
    assert "apps.rpiconnect" in script
    assert (
        "enabled_apps_lock --role Watchtower --feature-pack rpi_connect_updates --write"
        in script
    )
    assert "IMAGER_CONNECT_BASE_IMAGE_URI" in script
    assert "IMAGER_CONNECT_DOWNLOAD_BASE_URI" in script
    assert "--profile connect-ota" in script
    assert "--no-copy-parent-network" in script
    assert "--no-reserve" in script
    assert "register-connect-release" in script
    assert '"Terminal", "Satellite", "Control", "Watchtower"' in script


def _write_executable(path: Path, text: str) -> None:
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _make_stop_script_harness(tmp_path: Path) -> tuple[Path, Path, Path]:
    harness = tmp_path / "suite"
    helper_dir = harness / "scripts" / "helpers"
    helper_dir.mkdir(parents=True)
    script_path = harness / "stop.sh"
    script_path.write_text(_read("stop.sh"), encoding="utf-8", newline="\n")
    script_path.chmod(0o755)

    helper_text = """
        ARTHEXIS_SERVICE_MODE_EMBEDDED=embedded
        ARTHEXIS_RFID_SERVICE_LOCK=rfid_service.lck
        ARTHEXIS_CAMERA_SERVICE_LOCK=camera_service.lck

        arthexis_load_env_file() { :; }
        arthexis_resolve_log_dir() {
          local base="$1"
          local out="$2"
          mkdir -p "$base/logs"
          printf -v "$out" '%s' "$base/logs"
        }
        arthexis_detect_backend_port() { printf '%s\n' 8888; }
        arthexis_detect_service_mode() { printf '%s\n' embedded; }
        arthexis_prime_sudo_credentials() { return 1; }
        arthexis_clear_suite_uptime_lock() { :; }
        arthexis_lcd_feature_enabled() { return 1; }
        arthexis_stop_embedded_lcd_processes() { :; }
    """
    for helper_name in (
        "env.sh",
        "logging.sh",
        "ports.sh",
        "service_manager.sh",
        "suite-uptime-lock.sh",
    ):
        _write_executable(helper_dir / helper_name, helper_text)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    call_log = tmp_path / "calls.log"
    fake_command = """
        #!/usr/bin/env bash
        printf '%s %s\n' "$(basename "$0")" "$*" >> "$ARTHEXIS_TEST_COMMAND_LOG"
        if [ "$(basename "$0")" = "python3" ]; then
          printf '0 0\n'
          exit 0
        fi
        if [ "$(basename "$0")" = "sudo" ]; then
          exit 1
        fi
        exit 0
    """
    for command_name in ("pkill", "python3", "sudo", "systemctl"):
        _write_executable(bin_dir / command_name, fake_command)

    return script_path, bin_dir, call_log


def _find_usable_bash() -> str | None:
    candidates = [
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            result = subprocess.run(
                [candidate, "--version"],
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return candidate
    return None


def _run_harnessed_stop_script(
    tmp_path: Path, *args: str
) -> tuple[subprocess.CompletedProcess[str], Path]:
    bash = _find_usable_bash()
    if bash is None:
        pytest.skip("bash is required for stop.sh lifecycle contract tests")

    script_path, bin_dir, call_log = _make_stop_script_harness(tmp_path)
    env = os.environ.copy()
    env["ARTHEXIS_TEST_COMMAND_LOG"] = str(call_log)
    env["PATH"] = os.pathsep.join([str(bin_dir), env.get("PATH", "")])
    result = subprocess.run(
        [bash, str(script_path), *args],
        cwd=script_path.parent,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    return result, call_log


def test_stop_help_exits_before_stop_actions(tmp_path: Path) -> None:
    result, call_log = _run_harnessed_stop_script(tmp_path, "--help")

    assert result.returncode == 0
    assert "Usage: ./stop.sh" in result.stdout
    assert not call_log.exists(), result.stdout + result.stderr


def test_stop_unknown_flag_exits_before_stop_actions(tmp_path: Path) -> None:
    result, call_log = _run_harnessed_stop_script(tmp_path, "--bogus")

    assert result.returncode == 2
    assert "Unsupported stop.sh option: --bogus" in result.stderr
    assert "Usage: ./stop.sh" in result.stderr
    assert not call_log.exists(), result.stdout + result.stderr


def test_stop_script_omits_retired_audio_and_simulator_hooks() -> None:
    script_text = _read_shell_contract("stop.sh")

    assert "emit_shutdown_audio_alert" not in script_text
    assert "manage.py audio_alert shutdown" not in script_text
    assert "ARTHEXIS_AUDIO_ALERTS" not in script_text
    assert "ocpp_simulator" not in script_text
    assert "simulator.json" not in script_text


def test_env_refresh_omits_retired_browser_preview_bootstrap() -> None:
    script_text = _read_shell_contract("env-refresh.sh")

    assert "--preview-deps" not in script_text
    assert "ARTHEXIS_INSTALL_PREVIEW_DEPS" not in script_text
    assert "ARTHEXIS_SKIP_PLAYWRIGHT_INSTALL_DEPS" not in script_text
    assert "ensure_playwright" not in script_text
    assert "playwright install" not in script_text
    assert "ensure_selenium" not in script_text
    assert "Unsupported env-refresh.sh option: $1" in script_text
    assert (
        'if [[ "$DEPS_ONLY" -eq 1 ]]; then\n  INCLUDE_QA_REQUIREMENTS=1' in script_text
    )


def test_lifecycle_scripts_expose_documented_entrypoints() -> None:
    scripts_and_tokens = {
        "start.sh": ("--clear-logs", "--show LEVEL", "--reload", "--silent"),
        "stop.sh": ("Usage: ./stop.sh", "--all", "--force", "--confirm", "--help"),
        "status.sh": ("Usage: ./status.sh", "--wait", "--help"),
        "configure.sh": (
            "--feature SLUG",
            "--feature-param FEATURE:KEY=VALUE",
            "--repair",
            "--check",
        ),
        "upgrade.sh": (
            "--detached",
            "--reconcile",
            "--migrate",
            "--stop",
            "--branch",
            "--target-version",
            "--target-revision",
            "--target-tag",
        ),
        "error-report.sh": (
            "Usage: ./error-report.sh",
            "--output-dir DIR",
            "--upload-url URL",
            "--dry-run",
            "sys.version_info[0] == 3",
        ),
        "uninstall.sh": (
            "--service NAME",
            "--no-warn",
            "--rfid-service",
            "--no-rfid-service",
            "arthexis_remove_control_usb_polling_timer_overrides",
        ),
    }

    for script_name, tokens in scripts_and_tokens.items():
        script_text = _read_shell_contract(script_name)
        for token in tokens:
            assert (
                token in script_text
            ), f"{script_name} is missing expected token: {token}"

    upgrade_script = _read_shell_contract("upgrade.sh")
    assert re.search(
        r"^\s*--branch\)\s*$", upgrade_script, re.MULTILINE
    ), "upgrade.sh is missing expected parser label: --branch)"

    command_script = _read_shell_contract("command.sh")
    assert 'python -m utils.command_api "$@"' in command_script

    manual = MANUAL.read_text(encoding="utf-8")
    assert "`./command.sh list`" in manual
    assert "`./command.sh <operational-command> [args...]`" in manual
    assert "`error-report.sh` builds a single diagnostic zip" in manual


def test_tag_from_version_blocks_stale_existing_release_tag() -> None:
    workflow_text = _read(".github/workflows/tag-from-version.yml")

    assert "git fetch --force --tags origin" in workflow_text
    assert 'tag_sha="$(git rev-list -n 1 "refs/tags/${tag}")"' in workflow_text
    assert "Do not publish the stale tag." in workflow_text
    assert "move ${tag} to the reviewed ${GITHUB_REF_NAME} commit" in workflow_text
    assert "steps.create_tag.outputs.publish == 'true'" in workflow_text


def test_env_refresh_wrapper_requires_explicit_migration_write_flag() -> None:
    script_text = _read_shell_contract("env-refresh.sh")

    assert "WRITE_MIGRATIONS=0" in script_text
    assert "--write-migrations)" in script_text
    assert 'ARGS="$ARGS --write-migrations"' in script_text


def test_upgrade_waits_for_env_refresh_before_service_restart() -> None:
    script_text = _read_shell_contract("upgrade.sh")
    env_refresh_text = _read_shell_contract("env-refresh.sh")

    assert "ENV_REFRESH_PID_FILE" in script_text
    assert "ENV_REFRESH_PID_FILE" in env_refresh_text
    assert "ENV_REFRESH_PID_DIR" in script_text
    assert "ENV_REFRESH_PID_DIR" in env_refresh_text
    assert "write_env_refresh_pid_file()" in env_refresh_text
    assert "cleanup_env_refresh_pid_file()" in env_refresh_text
    assert 'pid_file="$ENV_REFRESH_PID_DIR/$$.pid"' in env_refresh_text
    assert "umask 077" in env_refresh_text
    assert 'umask 077 && mkdir -p "$ENV_REFRESH_PID_DIR"' in env_refresh_text
    assert "chmod 600" not in env_refresh_text
    assert "awk 'sub(/^.*\\)/, \"\") {print $20}'" in env_refresh_text
    assert "awk 'sub(/^.*\\)/, \"\") {print $20}'" in script_text
    assert 'physical_script_dir="$(cd "$SCRIPT_DIR" && pwd -P)"' in env_refresh_text
    assert 'expected_base_dir="$(env_refresh_physical_dir "$BASE_DIR")"' in script_text
    assert '(cd "$path" && pwd -P)' in script_text
    assert 'actual_cwd" != "$expected_base_dir"' in script_text
    assert 'for pid_file in "$ENV_REFRESH_PID_DIR"/*.pid; do' in script_text
    assert "trap cleanup_env_refresh_pid_file EXIT" in env_refresh_text
    assert env_refresh_text.index("  write_env_refresh_pid_file") < (
        env_refresh_text.index('bootstrap_python="$(arthexis_python_bin')
    )
    assert "wait_for_env_refresh_idle()" in script_text
    assert (
        "Timed out waiting for env-refresh to finish before restarting services."
        in script_text
    )
    assert "if ! wait_for_env_refresh_idle 300; then" in script_text
    assert 'pgrep -f "env-refresh' not in script_text
    assert script_text.index("wait_for_env_refresh_idle()") < script_text.index(
        "restart_services()"
    )
    assert script_text.index(
        "if ! wait_for_env_refresh_idle 300; then"
    ) < script_text.index(
        "local include_rfid=0",
        script_text.index("restart_services()"),
    )


def test_upgrade_restart_skips_unregistered_celery_systemd_units() -> None:
    script_text = _read_shell_contract("upgrade.sh")

    assert "celery_systemd_unit_present()" in script_text
    assert '_prefixed_systemd_unit_present "celery" "$1"' in script_text
    assert "celery_beat_systemd_unit_present()" in script_text
    assert '_prefixed_systemd_unit_present "celery-beat" "$1"' in script_text

    restart_index = script_text.index("restart_services()")
    celery_index = script_text.index(
        'if [ -f "$LOCK_DIR/celery.lck" ]',
        restart_index,
    )
    celery_block = script_text[
        celery_index : script_text.index('if [ "$include_rfid"', celery_index)
    ]

    assert 'local celery_unit="${celery_service}.service"' in celery_block
    assert 'local celery_beat_unit="${celery_beat_service}.service"' in celery_block
    assert (
        'arthexis_systemd_unit_recorded "$LOCK_DIR" "$celery_unit" && \\\n'
        '           celery_systemd_unit_present "$service_name"' in celery_block
    )
    assert (
        'arthexis_systemd_unit_recorded "$LOCK_DIR" "$celery_beat_unit" && \\\n'
        '           celery_beat_systemd_unit_present "$service_name"' in celery_block
    )


def test_service_start_script_supports_bind_host_env() -> None:
    script_text = _read("scripts/service-start.sh")

    assert "default_runserver_host()" in script_text
    assert (
        'if [[ -f "$LOCK_DIR/charger_facing.lck" || -f "$LOCK_DIR/ocpp_gateway.lck" ]]; then'
        in script_text
    )
    assert "charger_facing_disabled.lck" in script_text
    assert (
        "role_name=\"$(tr -d '[:space:]' < \"$LOCK_DIR/role.lck\" 2>/dev/null | tr '[:upper:]' '[:lower:]')\""
        in script_text
    )
    assert "satellite|control)" in script_text
    assert "printf '%s\\n' \"0.0.0.0\"" in script_text
    assert "printf '%s\\n' \"127.0.0.1\"" in script_text
    assert (
        'RUNSERVER_HOST="${ARTHEXIS_RUNSERVER_HOST:-$(default_runserver_host)}"'
        in script_text
    )
    assert 'RUNSERVER_BIND_HOST="$RUNSERVER_HOST"' in script_text
    assert r'if [[ "$RUNSERVER_BIND_HOST" == \[*\] ]]; then' in script_text
    assert r'if [[ "$RUNSERVER_BIND_HOST" == *:* ]]; then' in script_text
    assert (
        'python manage.py runserver "${RUNSERVER_HOST}:$PORT" "${RUNSERVER_EXTRA_ARGS[@]}" &'
        in script_text
    )
    assert (
        'python manage.py runserver "${RUNSERVER_HOST}:$PORT" --noreload "${RUNSERVER_EXTRA_ARGS[@]}" &'
        in script_text
    )
    assert (
        script_text.count(
            'if wait_for_suite_startup "$RUNSERVER_BIND_HOST" "$PORT" "$DJANGO_SERVER_PID" "$STARTUP_TIMEOUT"; then'
        )
        >= 2
    )


def test_service_start_waits_while_upgrade_progress_lock_exists() -> None:
    script_text = _read("scripts/service-start.sh")

    assert 'UPGRADE_IN_PROGRESS_LOCK="$LOCK_DIR/upgrade_in_progress.lck"' in script_text
    assert "expire_upgrade_progress_lock_if_stale" in script_text
    assert "wait_for_upgrade_progress_lock" in script_text
    assert "UPGRADE_IN_PROGRESS_LOCK_MAX_AGE_SECONDS" in script_text
    assert "UPGRADE_IN_PROGRESS_LOCK_WAIT_SECONDS" in script_text
    assert "ARTHEXIS_ALLOW_SERVICE_START_DURING_UPGRADE:-0" in script_text
    assert (
        "Upgrade in progress; waiting for upgrade to complete before service start."
        in script_text
    )
    assert script_text.index(
        'UPGRADE_IN_PROGRESS_LOCK="$LOCK_DIR/upgrade_in_progress.lck"'
    ) < (script_text.index("source .venv/bin/activate"))
    lock_assignment_index = script_text.index(
        'UPGRADE_IN_PROGRESS_LOCK="$LOCK_DIR/upgrade_in_progress.lck"'
    )
    wait_call_index = script_text.index(
        "wait_for_upgrade_progress_lock",
        lock_assignment_index,
    )
    assert wait_call_index < script_text.index("source .venv/bin/activate")

    wait_function_match = re.search(
        r"^wait_for_upgrade_progress_lock\(\)\s*\{\n(?P<body>.*?)^\}\n",
        script_text,
        re.MULTILINE | re.DOTALL,
    )
    assert wait_function_match, "service-start.sh is missing upgrade lock wait loop"
    wait_body = wait_function_match.group("body")
    assert "expire_upgrade_progress_lock_if_stale" in wait_body
    assert 'sleep "$UPGRADE_IN_PROGRESS_LOCK_WAIT_SECONDS"' in wait_body
    assert "exit 0" not in wait_body


def test_service_start_expires_stale_upgrade_progress_lock(tmp_path: Path) -> None:
    bash = _find_usable_bash()
    if bash is None:
        pytest.skip("bash is required for service-start.sh lifecycle contract tests")

    script_text = _read("scripts/service-start.sh")
    stale_function_match = re.search(
        r"^upgrade_progress_lock_is_stale\(\)\s*\{\n(?P<body>.*?)^\}\n",
        script_text,
        re.MULTILINE | re.DOTALL,
    )
    expire_function_match = re.search(
        r"^expire_upgrade_progress_lock_if_stale\(\)\s*\{\n(?P<body>.*?)^\}\n",
        script_text,
        re.MULTILINE | re.DOTALL,
    )
    assert stale_function_match, "service-start.sh is missing stale lock detection"
    assert expire_function_match, "service-start.sh is missing stale lock cleanup"

    harness = tmp_path / "expire-upgrade-lock.sh"
    lock_dir = tmp_path / ".locks"
    lock_file = lock_dir / "upgrade_in_progress.lck"
    _write_executable(
        harness,
        f"""
        #!/usr/bin/env bash
        set -euo pipefail
        LOCK_DIR="$1"
        UPGRADE_IN_PROGRESS_LOCK="$LOCK_DIR/upgrade_in_progress.lck"
        UPGRADE_IN_PROGRESS_LOCK_MAX_AGE_SECONDS="$2"
        upgrade_progress_lock_is_stale() {{
        {stale_function_match.group("body")}
        }}
        expire_upgrade_progress_lock_if_stale() {{
        {expire_function_match.group("body")}
        }}
        expire_upgrade_progress_lock_if_stale
        if [ -f "$UPGRADE_IN_PROGRESS_LOCK" ]; then
          echo present
        else
          echo expired
        fi
        """,
    )

    lock_dir.mkdir()
    lock_file.write_text("1970-01-01T00:00:00+00:00\n", encoding="utf-8")
    stale_result = subprocess.run(
        [bash, str(harness), str(lock_dir), "1"],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert stale_result.returncode == 0, stale_result.stdout + stale_result.stderr
    assert stale_result.stdout.strip().endswith("expired")

    lock_file.write_text("", encoding="utf-8")
    fresh_empty_result = subprocess.run(
        [bash, str(harness), str(lock_dir), "3600"],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert fresh_empty_result.returncode == 0, (
        fresh_empty_result.stdout + fresh_empty_result.stderr
    )
    assert fresh_empty_result.stdout.strip() == "present"

    lock_file.write_text("", encoding="utf-8")
    os.utime(lock_file, (0, 0))
    stale_empty_result = subprocess.run(
        [bash, str(harness), str(lock_dir), "1"],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert stale_empty_result.returncode == 0, (
        stale_empty_result.stdout + stale_empty_result.stderr
    )
    assert stale_empty_result.stdout.strip().endswith("expired")

    lock_file.write_text("not-a-date\n", encoding="utf-8")
    os.utime(lock_file, (0, 0))
    malformed_result = subprocess.run(
        [bash, str(harness), str(lock_dir), "1"],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert malformed_result.returncode == 0, (
        malformed_result.stdout + malformed_result.stderr
    )
    assert malformed_result.stdout.strip().endswith("expired")

    future_mtime = int(time.time()) + 3600
    lock_file.write_text("", encoding="utf-8")
    os.utime(lock_file, (future_mtime, future_mtime))
    future_empty_result = subprocess.run(
        [bash, str(harness), str(lock_dir), "1"],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert future_empty_result.returncode == 0, (
        future_empty_result.stdout + future_empty_result.stderr
    )
    assert future_empty_result.stdout.strip().endswith("expired")

    lock_file.write_text("not-a-date\n", encoding="utf-8")
    os.utime(lock_file, (future_mtime, future_mtime))
    future_malformed_result = subprocess.run(
        [bash, str(harness), str(lock_dir), "1"],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert future_malformed_result.returncode == 0, (
        future_malformed_result.stdout + future_malformed_result.stderr
    )
    assert future_malformed_result.stdout.strip().endswith("expired")

    tolerated_future_epoch = int(time.time()) + 60
    tolerated_future_timestamp = time.strftime(
        "%Y-%m-%dT%H:%M:%S+00:00",
        time.gmtime(tolerated_future_epoch),
    )
    lock_file.write_text(f"{tolerated_future_timestamp}\n", encoding="utf-8")
    tolerated_future_result = subprocess.run(
        [bash, str(harness), str(lock_dir), "1"],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
        env={
            **os.environ,
            "ARTHEXIS_UPGRADE_IN_PROGRESS_LOCK_MAX_FUTURE_SKEW_SECONDS": "300",
        },
    )
    assert tolerated_future_result.returncode == 0, (
        tolerated_future_result.stdout + tolerated_future_result.stderr
    )
    assert tolerated_future_result.stdout.strip() == "present"

    lock_file.write_text("2999-01-01T00:00:00+00:00\n", encoding="utf-8")
    fresh_result = subprocess.run(
        [bash, str(harness), str(lock_dir), "1"],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert fresh_result.returncode == 0, fresh_result.stdout + fresh_result.stderr
    assert fresh_result.stdout.strip().endswith("expired")

    lock_file.write_text("2999-01-01T00:00:00+00:00\n", encoding="utf-8")
    extreme_future_skew_result = subprocess.run(
        [
            bash,
            str(harness),
            str(lock_dir),
            "1",
        ],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
        env={
            **os.environ,
            "ARTHEXIS_UPGRADE_IN_PROGRESS_LOCK_MAX_FUTURE_SKEW_SECONDS": "999999999999",
        },
    )
    assert extreme_future_skew_result.returncode == 0, (
        extreme_future_skew_result.stdout + extreme_future_skew_result.stderr
    )
    assert extreme_future_skew_result.stdout.strip().endswith("expired")


def test_service_start_default_bind_host_keeps_explicit_charger_facing_mixed_address(
    tmp_path: Path,
) -> None:
    bash = _find_usable_bash()
    if bash is None:
        pytest.skip("bash is required for service-start.sh lifecycle contract tests")

    script_text = _read("scripts/service-start.sh")
    function_match = re.search(
        r"^default_runserver_host\(\)\s*\{\n(?P<body>.*?)^\}\n",
        script_text,
        re.MULTILINE | re.DOTALL,
    )
    assert function_match, "service-start.sh is missing default_runserver_host()"

    harness = tmp_path / "default-runserver-host.sh"
    _write_executable(
        harness,
        f"""
        #!/usr/bin/env bash
        set -euo pipefail
        LOCK_DIR="$1"
        default_runserver_host() {{
        {function_match.group("body")}
        }}
        default_runserver_host
        """,
    )

    def run_case(lock_dir: Path) -> str:
        result = subprocess.run(
            [bash, str(harness), str(lock_dir)],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.strip()

    empty_locks = tmp_path / "empty"
    empty_locks.mkdir()
    assert run_case(empty_locks) == "127.0.0.1"

    control_locks = tmp_path / "control-lock"
    control_locks.mkdir()
    (control_locks / "control.lck").write_text("", encoding="utf-8")
    assert run_case(control_locks) == "127.0.0.1"

    gateway_role = tmp_path / "gateway-role"
    gateway_role.mkdir()
    (gateway_role / "role.lck").write_text("Gateway\n", encoding="utf-8")
    assert run_case(gateway_role) == "127.0.0.1"

    control_role = tmp_path / "control-role"
    control_role.mkdir()
    (control_role / "role.lck").write_text("Control\n", encoding="utf-8")
    assert run_case(control_role) == "0.0.0.0"

    satellite_role = tmp_path / "satellite-role"
    satellite_role.mkdir()
    (satellite_role / "role.lck").write_text("Satellite\n", encoding="utf-8")
    assert run_case(satellite_role) == "0.0.0.0"

    satellite_opt_out = tmp_path / "satellite-opt-out"
    satellite_opt_out.mkdir()
    (satellite_opt_out / "role.lck").write_text("Satellite\n", encoding="utf-8")
    (satellite_opt_out / "charger_facing_disabled.lck").write_text("", encoding="utf-8")
    assert run_case(satellite_opt_out) == "127.0.0.1"

    terminal_role = tmp_path / "terminal-role"
    terminal_role.mkdir()
    (terminal_role / "role.lck").write_text("Terminal\n", encoding="utf-8")
    assert run_case(terminal_role) == "127.0.0.1"

    charger_facing_locks = tmp_path / "charger-facing-lock"
    charger_facing_locks.mkdir()
    (charger_facing_locks / "charger_facing.lck").write_text("", encoding="utf-8")
    assert run_case(charger_facing_locks) == "0.0.0.0"

    ocpp_gateway_locks = tmp_path / "ocpp-gateway-lock"
    ocpp_gateway_locks.mkdir()
    (ocpp_gateway_locks / "ocpp_gateway.lck").write_text("", encoding="utf-8")
    assert run_case(ocpp_gateway_locks) == "0.0.0.0"


def test_upgrade_stops_services_before_source_update() -> None:
    script_text = _read_shell_contract("upgrade.sh")

    assert "ensure_services_stopped_for_upgrade()" in script_text
    assert "git pull --rebase" in script_text
    assert "if [[ $FORCE_STOP -eq 1 || $STOP_ONLY -eq 0 ]]; then" in script_text
    assert "STOP_ARGS+=(--force)" in script_text
    assert (
        'if [[ $FORCE_STOP -eq 1 || $STOP_ONLY -eq 0 ]]; then\n        echo "Upgrade aborted even after forcing stop.'
        in script_text
    )
    assert "stop_running_instance 0\n\nif [[ $REVERT_UPGRADE" not in script_text
    source_update_index = script_text.index("git pull --rebase")
    stop_definition_index = script_text.index("ensure_services_stopped_for_upgrade()")
    stop_call_index = script_text.index(
        "ensure_services_stopped_for_upgrade", stop_definition_index + 1
    )
    assert stop_call_index < source_update_index


def test_upgrade_finalizer_does_not_send_completion_email() -> None:
    script_text = _read_shell_contract("upgrade.sh")

    assert "notify_upgrade_completion_email" not in script_text
    assert "ARTHEXIS_DISABLE_UPGRADE_COMPLETION_EMAIL" not in script_text
    assert '"manage.py"\n    "upgrade"\n    "notify"' not in script_text


def test_upgrade_failure_recovery_clears_progress_lock_before_restart() -> None:
    script_text = _read_shell_contract("upgrade.sh")

    recovery_index = script_text.index("upgrade_failure_recovery()")
    restart_index = script_text.index("if ! restart_services; then", recovery_index)
    cleanup_index = script_text.rindex(
        "cleanup_upgrade_progress_lock",
        recovery_index,
        restart_index,
    )
    assert cleanup_index < restart_index


def test_upgrade_quarantines_generated_paths_before_removal() -> None:
    script_text = _read_shell_contract("upgrade.sh")

    assert "remove_generated_path_before_upgrade()" in script_text
    assert "remove_generated_cleanup_path()" in script_text
    assert 'local cleanup_parent="$LOCK_DIR/generated-cleanup"' in script_text
    assert 'mv -- "$generated_path" "$staged_path"' in script_text
    assert 'rm -rf -- "$target"' in script_text
    assert "runtime cache state must not block upgrades" in script_text
    assert 'rm -rf -- "$generated_path"' not in script_text


def test_post_upgrade_hooks_are_one_shot_and_receive_context(tmp_path: Path) -> None:
    bash = _find_usable_bash()
    if bash is None:
        pytest.skip("bash is required for post-upgrade hook lifecycle tests")

    suite_dir = tmp_path / "suite"
    hook_dir = suite_dir / ".locks" / "post-upgrade.d"
    hook_dir.mkdir(parents=True)
    suite_arg = suite_dir.as_posix()
    lock_arg = (suite_dir / ".locks").as_posix()
    result_file = suite_dir / "hook-context.txt"
    ignored_hook = hook_dir / "15-ignored.sh"
    ignored_hook.write_text("exit 99\n", encoding="utf-8")

    _write_executable(
        hook_dir / "10-first.sh",
        """
        #!/usr/bin/env bash
        set -euo pipefail
        printf 'first:%s:%s:%s:%s:%s:%s:%s\\n' \
          "$ARTHEXIS_BASE_DIR" \
          "$ARTHEXIS_LOCK_DIR" \
          "$ARTHEXIS_PREVIOUS_REVISION" \
          "$ARTHEXIS_CURRENT_REVISION" \
          "$ARTHEXIS_TARGET_VERSION" \
          "$ARTHEXIS_UPGRADE_CHANNEL" \
          "$ARTHEXIS_SERVICE_NAME" >> "$ARTHEXIS_BASE_DIR/hook-context.txt"
        """,
    )
    _write_executable(
        hook_dir / "20-second.sh",
        """
        #!/usr/bin/env bash
        set -euo pipefail
        printf 'second:%s\\n' "$ARTHEXIS_POST_UPGRADE_HOOK" >> "$ARTHEXIS_BASE_DIR/hook-context.txt"
        """,
    )

    harness = tmp_path / "run-hooks.sh"
    _write_executable(
        harness,
        f"""
        #!/usr/bin/env bash
        set -euo pipefail
        source "{(ROOT / 'scripts/helpers/post-upgrade-hooks.sh').as_posix()}"
        LOCAL_REVISION=old-sha
        CURRENT_REVISION=new-sha
        REMOTE_REVISION=target-sha
        LOCAL_VERSION=0.1.0
        REMOTE_VERSION=0.2.0
        CHANNEL=stable
        SERVICE_NAME=arthexis
        PYTHON_BIN=/tmp/python
        arthexis_run_post_upgrade_hooks "{suite_arg}" "{lock_arg}"
        """,
    )

    result = subprocess.run(
        [bash, str(harness)],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (hook_dir / "10-first.sh").exists()
    assert not (hook_dir / "20-second.sh").exists()
    assert ignored_hook.exists()
    assert result_file.read_text(encoding="utf-8").splitlines() == [
        f"first:{suite_arg}:{lock_arg}:old-sha:new-sha:0.2.0:stable:arthexis",
        f"second:{(hook_dir / '20-second.sh').as_posix()}",
    ]


def test_post_upgrade_hook_failure_is_left_for_retry(tmp_path: Path) -> None:
    bash = _find_usable_bash()
    if bash is None:
        pytest.skip("bash is required for post-upgrade hook lifecycle tests")

    suite_dir = tmp_path / "suite"
    hook_dir = suite_dir / ".locks" / "post-upgrade.d"
    hook_dir.mkdir(parents=True)
    suite_arg = suite_dir.as_posix()
    lock_arg = (suite_dir / ".locks").as_posix()
    _write_executable(
        hook_dir / "10-fail.sh",
        """
        #!/usr/bin/env bash
        set -euo pipefail
        printf 'failed\\n' >> "$ARTHEXIS_BASE_DIR/hook-order.txt"
        exit 7
        """,
    )
    _write_executable(
        hook_dir / "20-skip.sh",
        """
        #!/usr/bin/env bash
        set -euo pipefail
        printf 'should-not-run\\n' >> "$ARTHEXIS_BASE_DIR/hook-order.txt"
        """,
    )
    harness = tmp_path / "run-hooks.sh"
    _write_executable(
        harness,
        f"""
        #!/usr/bin/env bash
        set -euo pipefail
        source "{(ROOT / 'scripts/helpers/post-upgrade-hooks.sh').as_posix()}"
        arthexis_run_post_upgrade_hooks "{suite_arg}" "{lock_arg}"
        """,
    )

    result = subprocess.run(
        [bash, str(harness)],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 7
    assert (hook_dir / "10-fail.sh").exists()
    assert (hook_dir / "20-skip.sh").exists()
    assert (suite_dir / "hook-order.txt").read_text(encoding="utf-8") == "failed\n"


def test_pending_post_upgrade_hook_detector_requires_executable_file(
    tmp_path: Path,
) -> None:
    bash = _find_usable_bash()
    if bash is None:
        pytest.skip("bash is required for post-upgrade hook lifecycle tests")

    suite_dir = tmp_path / "suite"
    hook_dir = suite_dir / ".locks" / "post-upgrade.d"
    hook_dir.mkdir(parents=True)
    suite_arg = suite_dir.as_posix()
    lock_arg = (suite_dir / ".locks").as_posix()
    hook_path = hook_dir / "10-not-executable.sh"
    hook_path.write_text("exit 0\n", encoding="utf-8")
    harness = tmp_path / "pending-hooks.sh"
    _write_executable(
        harness,
        f"""
        #!/usr/bin/env bash
        set -euo pipefail
        source "{(ROOT / 'scripts/helpers/post-upgrade-hooks.sh').as_posix()}"
        if arthexis_has_post_upgrade_hooks "{suite_arg}" "{lock_arg}"; then
          printf 'pending\\n'
          exit 0
        fi
        printf 'missing\\n'
        exit 0
        """,
    )

    initial = subprocess.run(
        [bash, str(harness)],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert initial.returncode == 0, initial.stdout + initial.stderr
    assert initial.stdout == "missing\n"

    _write_executable(
        hook_path,
        """
        #!/usr/bin/env bash
        exit 0
        """,
    )
    result = subprocess.run(
        [bash, str(harness)],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "pending\n"


def test_post_upgrade_hooks_preserve_original_revision_after_self_update_rerun() -> (
    None
):
    upgrade_text = _read_shell_contract("upgrade.sh")
    hook_text = _read_shell_contract("scripts/helpers/post-upgrade-hooks.sh")

    assert 'RERUN_INITIAL_REVISION=""' in upgrade_text
    assert 'RERUN_INITIAL_REVISION="${rerun_line#LOCAL_REVISION=}"' in upgrade_text
    assert 'UPGRADE_INITIAL_REVISION="$LOCAL_REVISION"' in upgrade_text
    assert 'UPGRADE_INITIAL_REVISION="$RERUN_INITIAL_REVISION"' in upgrade_text
    assert "printf 'LOCAL_REVISION=%s\\n' \"$UPGRADE_INITIAL_REVISION\"" in upgrade_text
    assert (
        'export ARTHEXIS_PREVIOUS_REVISION="${UPGRADE_INITIAL_REVISION:-${LOCAL_REVISION:-}}"'
        in hook_text
    )


def test_upgrade_retries_pending_post_upgrade_hooks_before_noop_exit() -> None:
    upgrade_text = _read_shell_contract("upgrade.sh")
    hook_text = _read_shell_contract("scripts/helpers/post-upgrade-hooks.sh")

    pending_hook_probe = (
        'if arthexis_has_post_upgrade_hooks "$BASE_DIR" "$LOCK_DIR"; then\n'
        "  POST_UPGRADE_HOOKS_PENDING=1\n"
        "fi"
    )

    assert "arthexis_has_post_upgrade_hooks()" in hook_text
    assert "POST_UPGRADE_HOOKS_PENDING=0" in upgrade_text
    assert pending_hook_probe in upgrade_text
    assert (
        "Pending post-upgrade hooks detected; retrying against current checkout "
        "before remote updates." not in upgrade_text
    )
    assert (
        "Pending post-upgrade hooks detected; continuing local refresh to retry them."
        in upgrade_text
    )
    assert (
        "Pending post-upgrade hooks detected; retrying against current checkout before same-version updates."
        in upgrade_text
    )
    same_version_mismatch = upgrade_text.index(
        'elif [[ -n "$REMOTE_REVISION" && -n "$LOCAL_REVISION" && "$LOCAL_REVISION" != "$REMOTE_REVISION" ]]; then'
    )
    pinned_same_version = upgrade_text.index(
        'if target_pin_requested; then\n      echo "Pinned release target requested; aligning working tree to $REMOTE_REVISION for version $REMOTE_VERSION."',
        same_version_mismatch,
    )
    pending_same_version = upgrade_text.index(
        'elif [[ $POST_UPGRADE_HOOKS_PENDING -eq 1 ]]; then\n      echo "Pending post-upgrade hooks detected; retrying against current checkout before same-version updates."',
        same_version_mismatch,
    )
    assert pinned_same_version < pending_same_version
    assert same_version_mismatch < upgrade_text.index(
        'elif [[ $POST_UPGRADE_HOOKS_PENDING -eq 1 ]]; then\n    echo "Pending post-upgrade hooks detected; continuing local refresh to retry them."'
    )
    assert "LOCAL_ONLY=1" in upgrade_text
    assert 'REMOTE_REVISION="$LOCAL_REVISION"' in upgrade_text
    assert (
        upgrade_text.index(
            "elif target_pin_requested; then\n"
            "  if [[ $PRE_CHECK -ne 1 ]]; then\n"
            '    reset_safe_git_changes "$NODE_ROLE_NAME"'
        )
        < same_version_mismatch
    )
    assert upgrade_text.index(
        'if arthexis_has_post_upgrade_hooks "$BASE_DIR" "$LOCK_DIR"; then'
    ) < upgrade_text.index('echo "Checking repository for updates..."')
    assert upgrade_text.index(
        'if arthexis_has_post_upgrade_hooks "$BASE_DIR" "$LOCK_DIR"; then'
    ) < upgrade_text.index('if [[ "$LOCAL_VERSION" != "$REMOTE_VERSION" ]]; then')
    assert re.search(
        r"elif \[\[ \$POST_UPGRADE_HOOKS_PENDING -eq 1 \]\]; then\s+"
        r"UPGRADE_NEEDED=1",
        upgrade_text,
        re.MULTILINE,
    )
    assert re.search(
        r"elif \[\[ \$POST_UPGRADE_HOOKS_PENDING -eq 1 \]\]; then\s+"
        r"echo \"Pending post-upgrade hooks detected; continuing local refresh to retry them\.\"\s+"
        r"LOCAL_ONLY=1\s+"
        r"REMOTE_REVISION=\"\$LOCAL_REVISION\"",
        upgrade_text,
        re.MULTILINE,
    )


def test_upgrade_pending_post_upgrade_hooks_preserve_check_and_offline_retry() -> None:
    upgrade_text = _read_shell_contract("upgrade.sh")

    assert (
        "elif [[ $POST_UPGRADE_HOOKS_PENDING -eq 1 ]]; then\n"
        '        echo "Warning: Continuing with local sources to retry pending post-upgrade hooks." >&2\n'
        '        REMOTE_REVISION="$LOCAL_REVISION"\n'
        '        REMOTE_VERSION="$LOCAL_VERSION"\n'
        "        LOCAL_ONLY=1" in upgrade_text
    )
    assert (
        "if [[ $CHECK_ONLY -eq 1 && $POST_UPGRADE_HOOKS_PENDING -eq 1 ]]; then\n"
        "  LOCAL_ONLY=1\n"
        '  REMOTE_REVISION="$LOCAL_REVISION"\n'
        "fi" in upgrade_text
    )
    assert upgrade_text.index(
        'echo "Checking repository for updates..."'
    ) < upgrade_text.index(
        "if [[ $CHECK_ONLY -eq 1 && $POST_UPGRADE_HOOKS_PENDING -eq 1 ]]; then"
    )
    assert upgrade_text.index(
        "if [[ $CHECK_ONLY -eq 1 && $POST_UPGRADE_HOOKS_PENDING -eq 1 ]]; then"
    ) < upgrade_text.index(
        'if [[ $LOCAL_ONLY -eq 1 ]]; then\n  echo "Skipping git pull for local refresh."'
    )


def test_upgrade_pre_check_self_update_rerun_continues_recovery(tmp_path: Path) -> None:
    bash = _find_usable_bash()
    if bash is None:
        pytest.skip("bash is required for upgrade.sh pre-check lifecycle tests")

    upgrade_text = _read("upgrade.sh")
    early_call = 'early_pre_check_report_and_exit "$@"'
    early_prefix = upgrade_text[: upgrade_text.index(early_call)]
    suite_dir = tmp_path / "suite"
    lock_dir = suite_dir / ".locks"
    lock_dir.mkdir(parents=True)
    helper_dir = tmp_path / "scripts" / "helpers"
    helper_dir.mkdir(parents=True)
    (helper_dir / "git_remote.sh").write_text("", encoding="utf-8")

    harness = tmp_path / "early-pre-check.sh"
    _write_executable(
        harness,
        f"""
        #!/usr/bin/env bash
        set -eE
        {early_prefix}
        BASE_DIR="{suite_dir.as_posix()}"
        early_pre_check_report_and_exit "$@"
        printf 'continued\n'
        """,
    )

    no_lock = subprocess.run(
        [bash, str(harness), "--pre-check", "--local"],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert no_lock.returncode == 0, no_lock.stdout + no_lock.stderr
    assert "Upgrade would run a local environment refresh." in no_lock.stdout
    assert "continued" not in no_lock.stdout

    (lock_dir / "upgrade_rerun_required.lck").write_text(
        "SERVICE_WAS_ACTIVE=1\n", encoding="utf-8"
    )
    with_lock = subprocess.run(
        [bash, str(harness), "--pre-check", "--local"],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert with_lock.returncode == 0, with_lock.stdout + with_lock.stderr
    assert "pending upgrade.sh self-update rerun" in with_lock.stdout
    assert "continued" in with_lock.stdout


def test_upgrade_pre_check_exits_before_mutating_upgrade_steps() -> None:
    upgrade_text = _read_shell_contract("upgrade.sh")
    git_remote_text = _read_shell_contract("scripts/helpers/git_remote.sh")

    early_function_start = upgrade_text.index("early_pre_check_report_and_exit() {")
    early_call = upgrade_text.index('early_pre_check_report_and_exit "$@"')
    early_function = upgrade_text[early_function_start:early_call]

    assert (
        re.search(r"^pre_check_report_and_exit\(\) \{", upgrade_text, re.MULTILINE)
        is None
    )
    assert (
        "if [[ $PRE_CHECK -eq 1 ]]; then\n" "  pre_check_report_and_exit\n" "fi"
    ) not in upgrade_text
    assert "github_raw_version_url_for_revision()" in upgrade_text
    assert "read_remote_version_without_ref_update()" in upgrade_text
    assert "read_remote_tag_revision_without_ref_update()" in upgrade_text
    assert "early_has_post_upgrade_hooks()" in upgrade_text
    assert "ARTHEXIS_POST_UPGRADE_HOOK_DIR:-$lock_dir/post-upgrade.d" in upgrade_text
    assert (
        'target_tag_revision="$(printf \'%s\\n\' "$tag_listing" | awk -v ref="$tag_ref^{}"'
        in upgrade_text
    )
    assert "Upgrade pre-check complete; no changes applied." in upgrade_text
    assert (
        "Upgrade would revert working tree to revision $revert_target." in upgrade_text
    )
    assert (
        "Pinned release targets cannot be combined with --latest/--unstable."
        in early_function
    )
    assert "post_upgrade_hooks_pending=1" in early_function
    assert "Upgrade would retry pending post-upgrade hooks." in early_function
    assert early_function.count("Upgrade would run because --force was provided.") == 2
    assert 'local_ref="refs/heads/$branch"' in early_function
    assert 'local_ref="refs/remotes/origin/$branch"' in early_function
    assert "requested_branch_remote_only=1" in early_function
    assert (
        "Upgrade would create local branch $branch from origin/$branch."
        in early_function
    )
    assert 'git show "$local_ref:VERSION"' in early_function
    assert 'git rev-parse "$local_ref"' in early_function
    assert 'echo "Unknown option: $1" >&2' in early_function
    assert (
        "Pinned version pre-check requires --target-tag or --target-revision."
        in early_function
    )
    assert (
        "Upgrade would resolve pinned version $target_version without applying it."
        not in upgrade_text
    )
    assert 'git rev-parse --verify "${target_revision}^{commit}"' in early_function
    assert "does not contain VERSION" in early_function
    assert "pinned release target VERSION is $target_revision_version" in early_function
    assert "pinned release target resolved to $target_tag_revision" in early_function
    assert (
        'read_remote_version_without_ref_update "$target_tag_revision"'
        in early_function
    )
    assert 'read_remote_tag_revision_without_ref_update "$target_tag"' in early_function
    assert 'read_remote_version_without_ref_update "$remote_revision"' in early_function
    assert (
        'arthexis_git_for_remote "$BASE_DIR" origin ls-remote --heads origin "$branch"'
        in early_function
    )
    assert (
        'arthexis_git_for_remote "$BASE_DIR" origin ls-remote --tags origin "$tag_ref" "$tag_ref^{}"'
        in upgrade_text
    )
    assert "read_remote_version_from_temporary_fetch" in upgrade_text
    assert 'if [[ -z "$remote_url" ]]; then' in upgrade_text
    assert (
        'if ! arthexis_git_url_uses_github_ssh "$remote_url"; then' not in upgrade_text
    )
    assert 'git -C "$repo_root" config --get core.sshCommand' in git_remote_text
    assert "StrictHostKeyChecking=accept-new" not in git_remote_text
    assert "arthexis_git_for_remote_url_with_repo()" in git_remote_text
    assert (
        'arthexis_git_for_remote_url_with_repo "$remote_url" "$BASE_DIR" -C "$tmp_dir" fetch --depth=1 --no-tags "$remote_url" "$revision"'
        in upgrade_text
    )
    assert (
        "Upgrade would update version $local_version -> $remote_version."
        in early_function
    )
    assert (
        "Unable to inspect origin/$branch VERSION without updating refs."
        in early_function
    )
    assert "fetch_branch_with_ref_repair" not in early_function
    assert "git fetch" not in early_function
    assert "git pull" not in early_function
    assert "reset_safe_git_changes" not in early_function
    assert '. "$BASE_DIR/scripts/helpers/git_remote.sh"' in upgrade_text
    assert (
        upgrade_text.index('. "$BASE_DIR/scripts/helpers/git_remote.sh"') < early_call
    )
    assert early_call < upgrade_text.index('export TZ="${TZ:-America/Monterrey}"')
    assert early_call < upgrade_text.index("arthexis_log_startup_event")
    assert early_call < upgrade_text.index(
        'arthexis_ensure_upstream_remotes "$BASE_DIR"'
    )
    assert early_call < upgrade_text.index('exec > >(tee "$LOG_FILE")')
    assert early_call < upgrade_text.index('mkdir -p "$LOCK_DIR"')
    assert early_call < upgrade_text.index(
        'printf "%s\\n" "$(date -Iseconds)" > "$UPGRADE_IN_PROGRESS_LOCK"'
    )
    assert early_call < upgrade_text.index("UPGRADE_NEEDED=0")
    assert early_call < upgrade_text.index("UPGRADE_PLANNED=1")
    assert early_call < upgrade_text.index(
        'auto_realign_branch_for_role "$NODE_ROLE_NAME" "$BRANCH"'
    )
    assert early_call < upgrade_text.index(
        'fetch_branch_with_ref_repair origin "$BRANCH"'
    )
    assert early_call < upgrade_text.index(
        "if [[ $CHECK_ONLY -ne 1 ]] && [[ $REVERT_UPGRADE -eq 0 ]] && [[ $VENV_PRESENT -eq 1 ]]; then"
    )
    assert early_call < upgrade_text.index("git pull --rebase")
    assert early_call < upgrade_text.index("ensure_services_stopped_for_upgrade")
    assert early_call < upgrade_text.index('run_env_refresh "env_refresh"')
    assert early_call < upgrade_text.index('restart_services "$RESTART_LCD_WITH_CORE"')


def test_upgrade_can_refresh_environment_before_stop_when_safe() -> None:
    script_text = _read_shell_contract("upgrade.sh")

    assert "pending_migrations_after_update()" in script_text
    assert "missing_migrations_after_update()" in script_text
    assert "risky_files_changed_for_pre_stop_refresh()" in script_text
    assert "can_refresh_environment_before_stop()" in script_text
    assert 'run_env_refresh "env_refresh_pre_stop"' in script_text
    assert (
        "Environment refresh completed before service stop; skipping stopped-service refresh."
        in script_text
    )
    restart_index = script_text.index('restart_services "$RESTART_LCD_WITH_CORE"')
    assert (
        script_text.rindex("cleanup_upgrade_progress_lock", 0, restart_index)
        < restart_index
    )
    assert "manage.py makemigrations --check --dry-run" in script_text
    assert "/migrations/" in script_text and "/fixtures/" in script_text
    assert "^env-refresh\\.(sh|py)$" in script_text
    assert "^scripts/" in script_text
    assert (
        script_text.count('if [[ -z "${PYTHON_BIN:-}" || ! -x "$PYTHON_BIN" ]]; then')
        == 3
    )
    assert re.search(
        r'if \[ "\$CLEAN" -eq 1 \]; then\s+'
        r'if ! confirm_database_deletion "Running upgrade with --clean"; then\s+'
        r'echo "Upgrade aborted by user\."\s+'
        r"exit 1\s+"
        r"fi\s+"
        r"ensure_services_stopped_for_upgrade",
        script_text,
        re.MULTILINE,
    )
    assert re.search(
        r"if \[\[ \$DEPENDENCY_REFRESH_REQUIRED -eq 1 \]\]; then\s+"
        r"echo \"Dependency changes detected; stopping services before modifying the runtime environment\.\"\s+"
        r"ensure_services_stopped_for_upgrade",
        script_text,
        re.MULTILINE,
    )
    assert re.search(
        r"if can_refresh_environment_before_stop; then\s+"
        r"echo \"No pending migrations or dependency changes detected; refreshing environment before stopping services\.\"\s+"
        r"run_env_refresh \"env_refresh_pre_stop\"\s+"
        r"ENV_REFRESH_COMPLETED_BEFORE_STOP=1\s+"
        r"else\s+"
        r"ensure_services_stopped_for_upgrade",
        script_text,
        re.MULTILINE,
    )
    for guard in (
        "pending_migrations_after_update",
        "missing_migrations_after_update",
        "risky_files_changed_for_pre_stop_refresh",
    ):
        assert re.search(
            rf"if {guard}; then\s+return 1\s+fi",
            script_text,
            re.MULTILINE,
        )


def test_upgrade_refreshes_role_enabled_apps_lock_after_dependency_refresh() -> None:
    script_text = _read_shell_contract("upgrade.sh")
    dependency_refresh_call = 'run_env_dependency_refresh "env_refresh_deps"'
    dependency_refresh_guard = (
        'if role_enabled_apps_lock_refresh_required "$NODE_ROLE_NAME" && \\\n'
        "   [[ $DEPENDENCY_REFRESH_REQUIRED -eq 1 ]]; then"
    )
    refresh_call = 'refresh_role_enabled_apps_lock "$NODE_ROLE_NAME"'
    lock_command = (
        '"$PYTHON_BIN" manage.py enabled_apps_lock '
        '--role="$node_role" --strict --write --preserve-application-disables '
        '"${include_args[@]}"'
    )
    dependency_refresh_command = (
        "FAILOVER_CREATED=1 ./env-refresh.sh --deps-only $ENV_ARGS"
    )
    env_refresh_command = "FAILOVER_CREATED=1 ./env-refresh.sh $ENV_ARGS"

    assert "refresh_role_enabled_apps_lock()" in script_text
    assert "role_enabled_apps_lock_refresh_required()" in script_text
    assert "role_app_profiles_explicitly_enabled_for_upgrade()" in script_text
    assert "role_app_profile_inputs_present_for_upgrade()" in script_text
    assert "role_app_lock_refresh_explicitly_enabled_for_upgrade()" in script_text
    assert "role_app_lock_preserve_direct_enabled_for_upgrade()" in script_text
    assert "existing_enabled_apps_lock_direct_includes()" in script_text
    assert "_BUILT_IN_APP_ENTRIES" in script_text
    assert "_normalize_selected_app_entries" in script_text
    assert "def selector_is_known(selector):" in script_text
    assert "values = {normalized}" in script_text
    assert 'values.add(normalized.rsplit(".", maxsplit=1)[-1])' in script_text
    assert "if not selector_is_known(selector):\n        continue" in script_text
    assert (
        "if charger_route_locks_present and normalized_entries & "
        "charger_facing_route_selectors:\n"
        "        continue" in script_text
    )
    assert 'charger_facing_route_selectors = {"apps.ocpp"}' in script_text
    assert "values = {normalized}" in script_text
    assert "values = {normalized, normalized.rsplit" not in script_text
    assert 'values.add(normalized.rsplit(".", maxsplit=1)[-1])' in script_text
    assert "read_enabled_apps_lock_direct_entries" in script_text
    assert "read_enabled_apps_lock_direct_sources" in script_text
    assert "get_direct_lock_app_selectors" not in script_text
    assert "current_direct_aliases" not in script_text
    assert "if selector_aliases & current_direct_aliases:" not in script_text
    assert "restore_charger_facing_enabled_apps_lock_metadata()" in script_text
    assert '"charger_facing.lck"' in script_text
    assert '"ocpp_gateway.lck"' in script_text
    assert "ocpp_direct_selectors = {" in script_text
    assert "if not ocpp_direct_selectors:" in script_text
    assert 'direct_sources["apps.ocpp"] = "charger-facing"' in script_text
    assert (
        'elif any(direct_sources.get(selector) == "charger-facing" '
        "for selector in ocpp_direct_selectors):" not in script_text
    )
    assert "write_enabled_apps_lock(" in script_text
    assert "ARTHEXIS_ROLE_APP_LOCK_REFRESH" in script_text
    assert "ARTHEXIS_ROLE_APP_LOCK_PRESERVE_DIRECT" in script_text
    assert 'case "${ARTHEXIS_ROLE_APP_LOCK_PRESERVE_DIRECT:-1}" in' in script_text
    assert "0|false|FALSE|False|no|NO|No|off|OFF|Off)" in script_text
    assert "run_env_dependency_refresh()" in script_text
    assert re.search(
        r'case "\$\{node_role,,\}" in\s+'
        r"control\)\s+"
        r'\[\[ -f "\$LOCK_DIR/enabled_apps\.lck" \]\] && return 0\s+'
        r";;\s+"
        r"satellite\|terminal\|watchtower\|constellation\)\s+"
        r'if \[\[ -f "\$LOCK_DIR/enabled_apps\.lck" \]\]; then\s+'
        r"role_app_lock_refresh_explicitly_enabled_for_upgrade && return 0\s+"
        r"role_app_profile_inputs_present_for_upgrade && return 0\s+"
        r"return 1\s+"
        r"fi\s+"
        r";;",
        script_text,
        re.MULTILINE,
    )
    assert "role_app_profiles_explicitly_enabled_for_upgrade && return 0" in script_text
    assert "role_app_profile_inputs_present_for_upgrade && return 0" in script_text
    assert '[[ -n "${ARTHEXIS_ROLE_APP_FEATURE_PACKS:-}" ]] && return 0' in script_text
    assert '[[ -n "${ARTHEXIS_FEATURE_PACKS:-}" ]] && return 0' in script_text
    assert '[[ -n "${ARTHEXIS_ROLE_APP_DISABLED_APPS:-}" ]] && return 0' in script_text
    assert '[[ -n "${ARTHEXIS_DISABLED_APPS:-}" ]] && return 0' in script_text
    assert "if role_app_lock_preserve_direct_enabled_for_upgrade; then" in script_text
    assert 'include_args+=(--include "$selector")' in script_text
    assert "Unable to inspect existing enabled-apps lock direct entries." in script_text
    assert lock_command in script_text
    assert dependency_refresh_command in script_text
    assert env_refresh_command in script_text
    assert "else\n    status=$?\n    arthexis_timing_end" in script_text
    assert dependency_refresh_guard in script_text
    assert (
        "Dependency changes detected; stopping services before enabled-apps lock dependency refresh."
        in script_text
    )
    assert re.search(
        rf"{re.escape(dependency_refresh_guard)}\s+"
        r'echo "Dependency changes detected; stopping services before enabled-apps lock dependency refresh\."\s+'
        r"ensure_services_stopped_for_upgrade\s+"
        rf"if ! {re.escape(dependency_refresh_call)}; then",
        script_text,
        re.MULTILINE,
    )
    assert (
        "Dependency refresh failed; aborting upgrade before enabled-apps lock refresh."
        in script_text
    )
    assert f"if ! {refresh_call}; then" in script_text
    assert (
        'echo "${NODE_ROLE_NAME} enabled-apps lock refresh failed; aborting upgrade."'
        in script_text
    )
    assert refresh_call in script_text
    assert script_text.index(
        'PYTHON_BIN="$VIRTUAL_ENV/bin/python"'
    ) < script_text.index(dependency_refresh_call)
    assert script_text.index(dependency_refresh_guard) < script_text.index(
        f"if ! {refresh_call}; then"
    )
    assert script_text.index(refresh_call) < script_text.index(
        "if can_refresh_environment_before_stop; then"
    )


def test_upgrade_preserves_explicit_route_provider_direct_include(
    tmp_path: Path,
) -> None:
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / "enabled_apps.lck").write_text(
        "# direct: apps.shop\n\napps.shop\n",
        encoding="utf-8",
    )

    assert _run_upgrade_existing_lock_direct_include_probe(tmp_path) == set()
