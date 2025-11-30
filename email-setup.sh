#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--status]

Options:
  --status    Display current email configuration without making changes.
  -h, --help  Show this help message and exit.
EOF
}

SHOW_STATUS=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --status)
      SHOW_STATUS=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

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

if [ "$SHOW_STATUS" = true ]; then
  "$PYTHON" manage.py shell <<'PYTHON'
from django.conf import settings
from teams.models import EmailInbox, EmailOutbox


def heading(title):
    print(f"\n{title}")
    print("-" * len(title))


def bool_text(value):
    return "yes" if value else "no"


def display_setting(value, default="(not set)"):
    return default if value in (None, "", []) else value


def display_bool_setting(name):
    value = getattr(settings, name, None)
    return "(not set)" if value in (None, "") else bool_text(value)


heading("Default Django email backend")
print(
    f"Backend: {display_setting(getattr(settings, 'EMAIL_BACKEND', None), 'django.core.mail.backends.smtp.EmailBackend')}"
)
print(
    f"Default from email: {display_setting(getattr(settings, 'DEFAULT_FROM_EMAIL', None))}"
)
print(f"EMAIL_HOST: {display_setting(getattr(settings, 'EMAIL_HOST', None))}")
print(f"EMAIL_PORT: {display_setting(getattr(settings, 'EMAIL_PORT', None))}")
print(f"EMAIL_HOST_USER: {display_setting(getattr(settings, 'EMAIL_HOST_USER', None))}")
password = getattr(settings, 'EMAIL_HOST_PASSWORD', None)
print(f"EMAIL_HOST_PASSWORD: {'set' if password else '(not set)'}")
print(f"EMAIL_USE_TLS: {display_bool_setting('EMAIL_USE_TLS')}")
print(f"EMAIL_USE_SSL: {display_bool_setting('EMAIL_USE_SSL')}")

heading("Configured Email Outboxes")
outboxes = list(
    EmailOutbox.objects.select_related("node", "user", "group").order_by("pk")
)
if not outboxes:
    print("(none)")
else:
    for outbox in outboxes:
        owners = []
        if outbox.node_id:
            owners.append(f"node='{outbox.node}'")
        if outbox.user_id:
            owners.append(f"user='{outbox.user}'")
        if outbox.group_id:
            owners.append(f"group='{outbox.group}'")
        owner = ", ".join(owners) if owners else "unattached"
        print(f"- Outbox #{outbox.pk} ({owner})")
        print(f"    host: {outbox.host or '(not set)'}")
        print(f"    port: {outbox.port}")
        username = outbox.username or '(not set)'
        print(f"    username: {username}")
        from_email = outbox.from_email or '(default sender)'
        print(f"    from email: {from_email}")
        print(f"    use_tls: {bool_text(outbox.use_tls)}")
        print(f"    use_ssl: {bool_text(outbox.use_ssl)}")
        print(f"    password stored: {'yes' if outbox.password else 'no'}")

heading("Configured Email Inboxes")
inboxes = list(EmailInbox.objects.select_related("user").order_by("pk"))
if not inboxes:
    print("(none)")
else:
    for inbox in inboxes:
        user_display = str(inbox.user) if inbox.user_id else "(no user)"
        print(f"- Inbox #{inbox.pk} (user='{user_display}')")
        print(f"    username: {inbox.username}")
        print(f"    host: {inbox.host}")
        print(f"    port: {inbox.port}")
        print(f"    protocol: {inbox.protocol}")
        print(f"    use_ssl: {bool_text(inbox.use_ssl)}")
        print(f"    password stored: {'yes' if inbox.password else 'no'}")
PYTHON
  exit 0
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
from teams.models import EmailOutbox
from apps.nodes.models import Node


defaults = {
    "host": "$HOST",
    "port": int("$PORT"),
    "username": "$USERNAME",
    "password": "$PASSWORD",
    "use_tls": "$USE_TLS".lower() == "y",
    "use_ssl": "$USE_SSL".lower() == "y",
    "from_email": "$FROM_EMAIL",
}

node = Node.get_local()
if node:
    outbox = EmailOutbox.objects.filter(node=node).first()
    if outbox is None:
        outbox = (
            EmailOutbox.objects.filter(node__isnull=True)
            .order_by("pk")
            .first()
        )
    if outbox is None:
        outbox = EmailOutbox(node=node)
    needs_node_update = outbox.node_id != getattr(node, "id", None)
    for field, value in defaults.items():
        setattr(outbox, field, value)
    if needs_node_update:
        outbox.node = node
    if outbox.pk is None:
        outbox.save()
    else:
        update_fields = list(defaults.keys())
        if needs_node_update:
            update_fields.append("node")
        outbox.save(update_fields=update_fields)
else:
    EmailOutbox.objects.update_or_create(
        id=1,
        defaults=defaults,
    )

print("Configured outbox")
PYTHON

    read -rp "Save this outbox as a User Datum? [y/N]: " SAVE_UD
    if [[ "${SAVE_UD,,}" == "y" ]]; then
        read -rp "Username: " UD_USER
        "$PYTHON" manage.py shell <<PYTHON
from django.contrib.auth import get_user_model
from apps.core.user_data import dump_user_fixture
from apps.nodes.models import Node
from teams.models import EmailOutbox


def _select_outbox(node):
    if node:
        outbox = EmailOutbox.objects.filter(node=node).first()
        if outbox:
            return outbox
        ownerless = EmailOutbox.objects.filter(node__isnull=True).order_by("pk").first()
        if ownerless:
            ownerless.node = node
            ownerless.save(update_fields=["node"])
            return ownerless
        return None
    return EmailOutbox.objects.order_by("pk").first()


node = Node.get_local()
outbox = _select_outbox(node)
if outbox is None:
    print("No EmailOutbox is configured for this installation.")
    raise SystemExit(1)

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
from teams.models import EmailInbox
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

