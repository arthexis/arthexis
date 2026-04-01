#!/usr/bin/env bash

arthexis_ensure_debug_toolbar_installed() {
  local python_bin="${1:-python}"
  local requirement="${ARTHEXIS_DEBUG_TOOLBAR_REQUIREMENT:-django-debug-toolbar==6.2.0}"

  if "$python_bin" - <<'PY' >/dev/null 2>&1
import importlib.util
import sys

sys.exit(0 if importlib.util.find_spec("debug_toolbar") is not None else 1)
PY
  then
    return 0
  fi

  echo "Debug mode requested; installing ${requirement} so the Django Debug Toolbar is available..."
  "$python_bin" -m pip install "$requirement"
}
