#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1

cd "$BASE_DIR"
PYTHON="$BASE_DIR/.venv/bin/python"
if [ ! -f "$PYTHON" ]; then
  echo "Virtual environment not found. Run ./install.sh first." >&2
  exit 1
fi

read -rp "Configure Email Outbox? [y/N]: " SET_OUTBOX
if [[ "${SET_OUTBOX,,}" == "y" ]]; then
    read -rp "SMTP host: " HOST
    read -rp "SMTP port [587]: " PORT
    PORT=${PORT:-587}
    read -rp "SMTP username: " USERNAME
    read -rsp "SMTP password: " PASSWORD
    echo
    read -rp "Use TLS? [Y/n]: " USE_TLS
    USE_TLS=${USE_TLS:-y}
    read -rp "Use SSL? [Y/n]: " USE_SSL
    USE_SSL=${USE_SSL:-y}
    read -rp "From email (leave blank to use default): " FROM_EMAIL

    "$PYTHON" manage.py shell <<PYTHON
from nodes.models import EmailOutbox
outbox, _ = EmailOutbox.objects.update_or_create(
    id=1,
    defaults={
        "host": "$HOST",
        "port": int("$PORT"),
        "username": "$USERNAME",
        "password": "$PASSWORD",
        "use_tls": "$USE_TLS".lower() == "y",
        "use_ssl": "$USE_SSL".lower() == "y",
        "from_email": "$FROM_EMAIL",
    },
)
print("Configured outbox")
PYTHON

    read -rp "Save this outbox as a User Datum? [y/N]: " SAVE_UD
    if [[ "${SAVE_UD,,}" == "y" ]]; then
        read -rp "Username: " UD_USER
        "$PYTHON" manage.py shell <<PYTHON
from django.contrib.auth import get_user_model
from core.user_data import dump_user_fixture
from nodes.models import Node, EmailOutbox
node = Node.get_local()
outbox = EmailOutbox.objects.get(node=node)
User = get_user_model()
user = User.objects.get(username="$UD_USER")
outbox.is_user_data = True
outbox.save(update_fields=["is_user_data"])
dump_user_fixture(outbox, user)
print("User datum created for", user.username)
PYTHON
    fi
fi

read -rp "Configure Email Inbox? [y/N]: " SET_INBOX
if [[ "${SET_INBOX,,}" == "y" ]]; then
    read -rp "App username: " INBOX_USER
    read -rp "Mailbox username: " INBOX_USERNAME
    read -rp "Mailbox host: " INBOX_HOST
    read -rp "Mailbox port [993]: " INBOX_PORT
    INBOX_PORT=${INBOX_PORT:-993}
    read -rsp "Mailbox password: " INBOX_PASSWORD
    echo
    read -rp "Protocol [imap/pop3]: " INBOX_PROTOCOL
    INBOX_PROTOCOL=${INBOX_PROTOCOL:-imap}
    read -rp "Use SSL? [Y/n]: " INBOX_SSL
    INBOX_SSL=${INBOX_SSL:-y}

    "$PYTHON" manage.py shell <<PYTHON
from django.contrib.auth import get_user_model
from core.models import EmailInbox
User = get_user_model()
user = User.objects.get(username="$INBOX_USER")
inbox, _ = EmailInbox.objects.update_or_create(
    user=user,
    username="$INBOX_USERNAME",
    defaults={
        "host": "$INBOX_HOST",
        "port": int("$INBOX_PORT"),
        "password": "$INBOX_PASSWORD",
        "protocol": "$INBOX_PROTOCOL",
        "use_ssl": "$INBOX_SSL".lower() == "y",
    },
)
print("Configured inbox for", user.username)
PYTHON
fi

