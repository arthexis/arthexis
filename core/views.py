import json
from datetime import date, timedelta

from django.contrib.auth import authenticate, login
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from utils.api import api_login_required

from .models import Product, Subscription, Account
from .models import RFID


@csrf_exempt
def rfid_login(request):
    """Authenticate a user using an RFID."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=400)

    try:
        data = json.loads(request.body.decode())
    except json.JSONDecodeError:
        data = request.POST

    rfid = data.get("rfid")
    if not rfid:
        return JsonResponse({"detail": "rfid required"}, status=400)

    user = authenticate(request, rfid=rfid)
    if user is None:
        return JsonResponse({"detail": "invalid RFID"}, status=401)

    login(request, user)
    return JsonResponse({"id": user.id, "username": user.username})


@api_login_required
def product_list(request):
    """Return a JSON list of products."""

    products = list(
        Product.objects.values("id", "name", "description", "renewal_period")
    )
    return JsonResponse({"products": products})


@csrf_exempt
@api_login_required
def add_subscription(request):
    """Create a subscription for an account from POSTed JSON."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=400)

    try:
        data = json.loads(request.body.decode())
    except json.JSONDecodeError:
        data = request.POST

    account_id = data.get("account_id")
    product_id = data.get("product_id")

    if not account_id or not product_id:
        return JsonResponse(
            {"detail": "account_id and product_id required"}, status=400
        )

    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        return JsonResponse({"detail": "invalid product"}, status=404)

    sub = Subscription.objects.create(
        account_id=account_id,
        product=product,
        next_renewal=date.today() + timedelta(days=product.renewal_period),
    )
    return JsonResponse({"id": sub.id})


@api_login_required
def subscription_list(request):
    """Return subscriptions for the given account_id."""

    account_id = request.GET.get("account_id")
    if not account_id:
        return JsonResponse({"detail": "account_id required"}, status=400)

    subs = list(
        Subscription.objects.filter(account_id=account_id)
        .select_related("product")
        .values(
            "id",
            "product__name",
            "next_renewal",
        )
    )
    return JsonResponse({"subscriptions": subs})


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
        tags = [
            {
                "rfid": t.rfid,
                "accounts": list(t.accounts.values_list("id", flat=True)),
                "allowed": t.allowed,
                "color": t.color,
                "released": t.released,
            }
            for t in qs.order_by("rfid")
        ]
        return JsonResponse({"rfids": tags})

    if request.method == "POST":
        try:
            data = json.loads(request.body.decode())
        except json.JSONDecodeError:
            return JsonResponse({"detail": "invalid JSON"}, status=400)

        tags = data.get("rfids") if isinstance(data, dict) else data
        if not isinstance(tags, list):
            return JsonResponse({"detail": "rfids list required"}, status=400)

        count = 0
        for row in tags:
            rfid = (row.get("rfid") or "").strip()
            if not rfid:
                continue
            allowed = row.get("allowed", True)
            accounts = row.get("accounts") or []
            color = (
                (row.get("color") or RFID.BLACK).strip().upper() or RFID.BLACK
            )
            released = row.get("released", False)
            if isinstance(released, str):
                released = released.lower() == "true"

            tag, _ = RFID.objects.update_or_create(
                rfid=rfid.upper(),
                defaults={
                    "allowed": allowed,
                    "color": color,
                    "released": released,
                },
            )
            if accounts:
                tag.accounts.set(Account.objects.filter(id__in=accounts))
            else:
                tag.accounts.clear()
            count += 1

        return JsonResponse({"imported": count})

    return JsonResponse({"detail": "GET or POST required"}, status=400)
