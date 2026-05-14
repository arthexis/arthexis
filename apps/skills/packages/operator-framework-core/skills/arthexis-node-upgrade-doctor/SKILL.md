---
name: arthexis-node-upgrade-doctor
description: Run and diagnose repo-native Arthexis install, upgrade, env refresh, migrations, local node role setup, and health checks across Windows and Linux. Use when the operator asks to upgrade the suite, install a node, start a local Terminal role, repair bootstrap state, or explain failed install-health output.
---

# Arthexis Node Upgrade Doctor

## Quick Start

Use repo-native scripts before manual bootstrap. This skill is for local Arthexis checkout health, install, upgrade, and validation.

Read-only state:

```powershell
python "$env:CODEX_HOME\skills\arthexis-node-upgrade-doctor\scripts\check_bootstrap_state.py" --repo [CONF.BASE_DIR]
python "$env:CODEX_HOME\skills\arthexis-node-upgrade-doctor\scripts\node_upgrade_doctor.py" plan --repo [CONF.BASE_DIR] --latest --role Terminal
```

Act only with `--write`:

```powershell
python "$env:CODEX_HOME\skills\arthexis-node-upgrade-doctor\scripts\node_upgrade_doctor.py" upgrade --repo [CONF.BASE_DIR] --latest --write
python "$env:CODEX_HOME\skills\arthexis-node-upgrade-doctor\scripts\node_upgrade_doctor.py" validate --repo [CONF.BASE_DIR] --write
```

## Rules

- If `.venv` is missing, use `install.bat` or `install.sh`. Do not hand-roll virtualenv setup first.
- Use `upgrade.bat --latest` or `upgrade.sh --latest` for latest suite upgrades.
- Use env refresh only after bootstrap exists.
- Preserve dirty worktrees. Inspect `git status` before commands that update code.
- When validation exposes a docs or script mismatch, fix the mismatch instead of reporting only the runtime failure.

## Scripts

- `scripts/check_bootstrap_state.py`: inspect repo, scripts, virtualenv, Python, git branch, and dirty state.
- `scripts/node_upgrade_doctor.py`: plan or execute install, upgrade, health, and validation commands with Windows/Linux command selection.
