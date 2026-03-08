import hashlib
import hmac
import logging
import time

from django.contrib import admin
from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.nodes.models import NetMessage, Node

from .admin import SlackBotProfileAdmin

from .models import SlackBotProfile, SlackApiError


logger = logging.getLogger(__name__)


class SlackBotOAuthCallbackView(View):
    """Handle the Slack OAuth callback using the admin wizard logic."""

    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        admin_view = SlackBotProfileAdmin(SlackBotProfile, admin.site)
        return admin_view.bot_creation_callback_view(request)


@method_decorator(csrf_exempt, name="dispatch")
class SlackCommandView(View):
    """Handle Slack slash commands for the Arthexis chatbot."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        payload = request.POST
        team_id = (payload.get("team_id") or "").strip().upper()
        if not team_id:
            return HttpResponse(status=400)

        bot = (
            SlackBotProfile.objects.filter(team_id=team_id, is_enabled=True)
            .select_related("node")
            .first()
        )
        if bot is None:
            return JsonResponse(
                {
                    "response_type": "ephemeral",
                    "text": _(
                        "This Slack workspace is not linked to a chatbot on this network."
                    ),
                },
                status=403,
            )

        if not self._verify_signature(bot, request):
            return HttpResponse(status=401)

        local = Node.get_local()
        if bot.node_id and local and bot.node_id != local.pk:
            return JsonResponse(
                {
                    "response_type": "ephemeral",
                    "text": _(
                        "This command must be routed through the node that owns the Slack bot."
                    ),
                },
                status=403,
            )

        try:
            return self._handle_command(bot, payload)
        except SlackApiError as exc:
            logger.exception("Slack API error while handling command for bot %s", bot.pk)
            return JsonResponse(
                {
                    "response_type": "ephemeral",
                    "text": _("Slack rejected the request: %(error)s")
                    % {"error": str(exc)},
                },
                status=502,
            )
        except Exception:  # pragma: no cover - unexpected runtime error
            logger.exception("Unhandled Slack command failure for bot %s", bot.pk)
            return JsonResponse(
                {
                    "response_type": "ephemeral",
                    "text": _("Unable to process the command right now."),
                },
                status=500,
            )

    # Internal helpers -------------------------------------------------

    def _verify_signature(self, bot: SlackBotProfile, request) -> bool:
        secret = bot.get_signing_secret()
        if not secret:
            return False

        timestamp = request.headers.get("X-Slack-Request-Timestamp") or request.META.get(
            "HTTP_X_SLACK_REQUEST_TIMESTAMP"
        )
        signature = request.headers.get("X-Slack-Signature") or request.META.get(
            "HTTP_X_SLACK_SIGNATURE"
        )
        if not timestamp or not signature:
            return False
        try:
            timestamp_int = int(timestamp)
        except (TypeError, ValueError):
            return False

        if abs(time.time() - timestamp_int) > 300:
            return False

        basestring = f"v0:{timestamp}:{request.body.decode('utf-8')}"
        expected = hmac.new(secret.encode("utf-8"), basestring.encode("utf-8"), hashlib.sha256)
        expected_signature = f"v0={expected.hexdigest()}"
        return hmac.compare_digest(expected_signature, signature)

    def _handle_command(self, bot: SlackBotProfile, payload) -> JsonResponse:
        text = (payload.get("text") or "").strip()
        if not text:
            return self._usage_response()

        verb, _, remainder = text.partition(" ")
        command = verb.lower()
        arguments = remainder.strip()

        if command in {"net", "net-message", "netmessage"}:
            return self._handle_net_message(bot, payload, arguments)

        return JsonResponse(
            {
                "response_type": "ephemeral",
                "text": _(
                    "Unknown command. Use `net <subject> | <body>` to broadcast a Net Message."
                ),
            }
        )

    def _handle_net_message(self, bot: SlackBotProfile, payload, text: str) -> JsonResponse:
        text = (text or "").strip()
        subject = ""
        body = ""
        if text:
            if "|" in text:
                subject_part, body_part = text.split("|", 1)
                subject = subject_part.strip()
                body = body_part.strip()
            else:
                body = text

        if subject and not body:
            body = subject
            subject = ""

        body = body.strip()
        if not body:
            return JsonResponse(
                {
                    "response_type": "ephemeral",
                    "text": _(
                        "Provide the message body after `net`. Optionally separate subject and body with `|`."
                    ),
                }
            )

        metadata_parts = []
        user_name = (payload.get("user_name") or "").strip()
        user_id = (payload.get("user_id") or "").strip()
        if user_name and user_id:
            metadata_parts.append(_("User %(name)s (%(identifier)s)") % {"name": user_name, "identifier": user_id})
        elif user_name:
            metadata_parts.append(_("User %(name)s") % {"name": user_name})
        elif user_id:
            metadata_parts.append(_("User ID %(identifier)s") % {"identifier": user_id})

        channel_name = (payload.get("channel_name") or "").strip()
        channel_id = (payload.get("channel_id") or "").strip()
        if channel_name and channel_id:
            metadata_parts.append(
                _("Channel %(name)s (%(identifier)s)")
                % {"name": channel_name, "identifier": channel_id}
            )
        elif channel_name:
            metadata_parts.append(_("Channel %(name)s") % {"name": channel_name})
        elif channel_id:
            metadata_parts.append(_("Channel ID %(identifier)s") % {"identifier": channel_id})

        if metadata_parts:
            metadata_text = _("Sent from Slack (%(details)s).") % {
                "details": "; ".join(metadata_parts)
            }
            combined = f"{body}\n\n{metadata_text}".strip()
            body = combined[:256]

        message = NetMessage.broadcast(subject=subject, body=body)

        summary = message.subject.strip() if message.subject else message.body.strip()
        if len(summary) > 60:
            summary = f"{summary[:57]}..."

        return JsonResponse(
            {
                "response_type": "ephemeral",
                "text": _("Net Message broadcast: %(summary)s") % {"summary": summary or "-"},
            }
        )

    @staticmethod
    def _usage_response() -> JsonResponse:
        return JsonResponse(
            {
                "response_type": "ephemeral",
                "text": _(
                    "Usage: `net <subject> | <body>` — the subject is optional;"
                    " include the body to broadcast a Net Message."
                ),
            }
        )
