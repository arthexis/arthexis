import json
from datetime import date, timedelta

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import Product, Subscription


def product_list(request):
    """Return a JSON list of products."""
    products = list(Product.objects.values("id", "name", "description", "renewal_period"))
    return JsonResponse({"products": products})


@csrf_exempt
def add_subscription(request):
    """Create a subscription for a user to a product from POSTed JSON."""
    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=400)

    try:
        data = json.loads(request.body.decode())
    except json.JSONDecodeError:
        data = request.POST

    user_id = data.get("user_id")
    product_id = data.get("product_id")

    if not user_id or not product_id:
        return JsonResponse({"detail": "user_id and product_id required"}, status=400)

    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        return JsonResponse({"detail": "invalid product"}, status=404)

    sub = Subscription.objects.create(
        user_id=user_id,
        product=product,
        next_renewal=date.today() + timedelta(days=product.renewal_period),
    )
    return JsonResponse({"id": sub.id})


def subscription_list(request):
    """Return subscriptions for the given user_id."""
    user_id = request.GET.get("user_id")
    if not user_id:
        return JsonResponse({"detail": "user_id required"}, status=400)

    subs = list(
        Subscription.objects.filter(user_id=user_id)
        .select_related("product")
        .values(
            "id",
            "product__name",
            "next_renewal",
        )
    )
    return JsonResponse({"subscriptions": subs})
