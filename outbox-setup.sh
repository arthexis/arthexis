#!/usr/bin/env bash
set -e
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$BASE_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1
cd "$BASE_DIR"

read -rp "SMTP host: " HOST
read -rp "SMTP port [587]: " PORT
PORT=${PORT:-587}
read -rp "SMTP username: " USERNAME
read -rsp "SMTP password: " PASSWORD
echo
read -rp "Use TLS? [y/N]: " USE_TLS
read -rp "Use SSL? [y/N]: " USE_SSL
read -rp "From email (leave blank to use default): " FROM_EMAIL

python manage.py shell <<PYTHON
from nodes.models import Node, EmailOutbox
node, _ = Node.register_current()
outbox, _ = EmailOutbox.objects.update_or_create(
    node=node,
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
print("Configured outbox for", node.hostname)
PYTHON

read -rp "Save this outbox as a User Datum? [y/N]: " SAVE_UD
if [[ "${SAVE_UD,,}" == "y" ]]; then
    read -rp "Username: " UD_USER
    python manage.py shell <<PYTHON
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from core.user_data import UserDatum
from nodes.models import Node, EmailOutbox
node = Node.get_local()
outbox = EmailOutbox.objects.get(node=node)
User = get_user_model()
user = User.objects.get(username="$UD_USER")
ct = ContentType.objects.get_for_model(EmailOutbox)
UserDatum.objects.get_or_create(user=user, content_type=ct, object_id=outbox.pk)
print("User datum created for", user.username)
PYTHON
fi
