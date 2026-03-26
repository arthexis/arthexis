"""RFID batch API endpoints.

Contract note:
* ``GET /core/rfids/`` does not expose command fields.
* ``POST /core/rfids/`` rejects ``external_command`` and ``post_auth_command``.
"""

from __future__ import annotations

import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from apps.cards.models import RFID
from apps.energy.models import CustomerAccount
from utils.api import api_login_required

_DISALLOWED_COMMAND_FIELDS = ("external_command", "post_auth_command")


@csrf_exempt
@api_login_required
def rfid_batch(request):
    """Export or import RFID tags in batch."""

    if request.method == "GET":
        color = request.GET.get("color", RFID.BLACK).upper()
        released = request.GET.get("released")
        if released is not None:
            released = released.lower()
        qs = RFID.objects.all()
        if color != "ALL":
            qs = qs.filter(color=color)
        if released in ("true", "false"):
            qs = qs.filter(released=(released == "true"))
        tags = []
        for t in qs.order_by("rfid"):
            ids = list(t.energy_accounts.values_list("id", flat=True))
            names = list(
                t.energy_accounts.exclude(name="").values_list("name", flat=True)
            )
            payload = {
                "rfid": t.rfid,
                "custom_label": t.custom_label,
                "customer_accounts": ids,
                "customer_account_names": names,
                "allowed": t.allowed,
                "color": t.color,
                "released": t.released,
            }
            payload["energy_accounts"] = ids
            payload["energy_account_names"] = names
            tags.append(payload)
        return JsonResponse({"rfids": tags})

    if request.method == "POST":
        try:
            data = json.loads(request.body.decode())
        except json.JSONDecodeError:
            return JsonResponse({"detail": "invalid JSON"}, status=400)

        tags = data.get("rfids") if isinstance(data, dict) else data
        if not isinstance(tags, list):
            return JsonResponse({"detail": "rfids list required"}, status=400)

        for index, row in enumerate(tags):
            disallowed = sorted(
                field for field in _DISALLOWED_COMMAND_FIELDS if field in row
            )
            if disallowed:
                return JsonResponse(
                    {
                        "detail": (
                            "Command fields are not accepted by this endpoint."
                        ),
                        "index": index,
                        "fields": disallowed,
                    },
                    status=400,
                )

        count = 0
        for row in tags:
            rfid = (row.get("rfid") or "").strip()
            if not rfid:
                continue
            allowed = row.get("allowed", True)
            energy_accounts = (
                row.get("customer_accounts")
                or row.get("energy_accounts")
                or []
            )
            account_names = row.get("customer_account_names") or row.get(
                "energy_account_names"
            )
            color = (row.get("color") or RFID.BLACK).strip().upper() or RFID.BLACK
            released = row.get("released", False)
            if isinstance(released, str):
                released = released.lower() == "true"
            custom_label = (row.get("custom_label") or "").strip()
<<<<<<< lab/deprecate-external_command-and-post_auth_command
            pre_auth_action = row.get("pre_auth_action")
            if not isinstance(pre_auth_action, str):
                pre_auth_action = ""
            else:
                pre_auth_action = pre_auth_action.strip().lower()
            post_auth_action = row.get("post_auth_action")
            if not isinstance(post_auth_action, str):
                post_auth_action = ""
            else:
                post_auth_action = post_auth_action.strip().lower()
=======
>>>>>>> main

            tag, _ = RFID.update_or_create_from_code(
                rfid,
                {
                    "allowed": allowed,
                    "color": color,
                    "released": released,
                    "custom_label": custom_label,
<<<<<<< lab/deprecate-external_command-and-post_auth_command
                    "pre_auth_action": pre_auth_action,
                    "post_auth_action": post_auth_action,
=======
>>>>>>> main
                },
            )
            accounts_qs = CustomerAccount.objects.none()
            if energy_accounts:
                accounts_qs = CustomerAccount.objects.filter(id__in=energy_accounts)
            elif account_names:
                names = [
                    value.strip()
                    for value in str(account_names).split(",")
                    if value.strip()
                ]
                accounts_qs = CustomerAccount.objects.filter(name__in=names)
            if accounts_qs:
                tag.energy_accounts.set(accounts_qs)
            else:
                tag.energy_accounts.clear()
            count += 1

        return JsonResponse({"imported": count})

    return JsonResponse({"detail": "GET or POST required"}, status=400)
