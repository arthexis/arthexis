#!/usr/bin/env bash
#
# ws.sh — Persistent workspace using GNU screen
# Creates or reattaches the screen session named "main" safely and idempotently.
#
# Usage:
#   ./ws.sh          # run manually
#   source ws.sh     # add to your shell rc file for auto-attach
#
# Recommend adding an alias:
#   alias ws="bash ~/ws.sh"
#

SESSION_NAME="main"

# 1) Only attempt to attach if shell is interactive
case $- in
    *i*) ;;   # ok: interactive
    *) return 0 2>/dev/null || exit 0 ;;   # non-interactive: do nothing
esac

# 2) Avoid recursion (if already inside screen)
if [[ -n "$STY" ]]; then
    # already inside a screen session → do nothing
    return 0 2>/dev/null || exit 0
fi

# 3) If session exists, attach. If not, create it.
screen -xRR "$SESSION_NAME"
