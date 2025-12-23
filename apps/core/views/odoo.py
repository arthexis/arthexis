from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone as datetime_timezone

from django.contrib.admin.sites import site as admin_site
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404, JsonResponse
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt

from apps.energy.models import CustomerAccount
from apps.odoo.models import OdooEmployee, OdooProduct
from utils.api import api_login_required

logger = logging.getLogger(__name__)


@staff_member_required
def odoo_products(request):
    """Return available products from the user's Odoo instance."""

    profile = getattr(request.user, "odoo_employee", None)
    if not profile or not profile.is_verified:
        raise Http404
    try:
        products = profile.execute(
            "product.product",
            "search_read",
            fields=["name"],
            limit=50,
        )
    except Exception:
        logger.exception(
            "Failed to fetch Odoo products via API for user %s (profile_id=%s, host=%s, database=%s)",
            getattr(request.user, "pk", None),
            getattr(profile, "pk", None),
            getattr(profile, "host", None),
            getattr(profile, "database", None),
        )
        return JsonResponse({"detail": "Unable to fetch products"}, status=502)
    items = [{"id": p.get("id"), "name": p.get("name", "")} for p in products]
    return JsonResponse(items, safe=False)


@staff_member_required
def odoo_quote_report(request):
    """Display a consolidated quote report from the user's Odoo instance."""

    profile = getattr(request.user, "odoo_employee", None)
    context = {
        "title": _("Quote Report"),
        "profile": profile,
        "error": None,
        "template_stats": [],
        "quotes": [],
        "recent_products": [],
        "installed_modules": [],
        "profile_url": "",
    }

    profile_admin = admin_site._registry.get(OdooEmployee)
    if profile_admin is not None:
        try:
            context["profile_url"] = profile_admin.get_my_profile_url(request)
        except Exception:  # pragma: no cover - defensive fallback
            context["profile_url"] = ""

    if not profile or not profile.is_verified:
        context["error"] = _(
            "Configure and verify your Odoo employee before generating the report."
        )
        return TemplateResponse(
            request, "admin/core/odoo_quote_report.html", context
        )

    def _parse_datetime(value):
        if not value:
            return None
        if isinstance(value, datetime):
            dt = value
        else:
            text = str(value)
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                text_iso = text.replace(" ", "T")
                try:
                    dt = datetime.fromisoformat(text_iso)
                except ValueError:
                    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
                        try:
                            dt = datetime.strptime(text, fmt)
                            break
                        except ValueError:
                            continue
                    else:
                        return None
        if timezone.is_naive(dt):
            tzinfo = getattr(timezone, "utc", datetime_timezone.utc)
            dt = timezone.make_aware(dt, tzinfo)
        return dt

    try:
        templates = profile.execute(
            "sale.order.template",
            "search_read",
            fields=["name"],
            order="name asc",
        )
        template_usage = profile.execute(
            "sale.order",
            "read_group",
            [[("sale_order_template_id", "!=", False)]],
            ["sale_order_template_id"],
            lazy=False,
        )

        usage_map = {}
        for entry in template_usage:
            template_info = entry.get("sale_order_template_id")
            if not template_info:
                continue
            template_id = template_info[0]
            usage_map[template_id] = entry.get(
                "sale_order_template_id_count", 0
            )

        context["template_stats"] = [
            {
                "id": template.get("id"),
                "name": template.get("name", ""),
                "quote_count": usage_map.get(template.get("id"), 0),
            }
            for template in templates
        ]

        ninety_days_ago = timezone.now() - timedelta(days=90)
        quotes = profile.execute(
            "sale.order",
            "search_read",
            [
                [
                    ("create_date", ">=", ninety_days_ago.strftime("%Y-%m-%d %H:%M:%S")),
                    ("state", "!=", "cancel"),
                    ("quote_sent", "=", False),
                ]
            ],
            fields=[
                "name",
                "amount_total",
                "partner_id",
                "activity_type_id",
                "activity_summary",
                "tag_ids",
                "create_date",
                "currency_id",
            ],
            order="create_date desc",
        )

        tag_ids = set()
        currency_ids = set()
        for quote in quotes:
            tag_ids.update(quote.get("tag_ids") or [])
            currency_info = quote.get("currency_id")
            if (
                isinstance(currency_info, (list, tuple))
                and len(currency_info) >= 1
                and currency_info[0]
            ):
                currency_ids.add(currency_info[0])

        tag_map: dict[int, str] = {}
        if tag_ids:
            tag_records = profile.execute(
                "sale.order.tag",
                "read",
                list(tag_ids),
                fields=["name"],
            )
            for tag in tag_records:
                tag_id = tag.get("id")
                if tag_id is not None:
                    tag_map[tag_id] = tag.get("name", "")

        currency_map: dict[int, dict[str, str]] = {}
        if currency_ids:
            currency_records = profile.execute(
                "res.currency",
                "read",
                list(currency_ids),
                fields=["name", "symbol"],
            )
            for currency in currency_records:
                currency_id = currency.get("id")
                if currency_id is not None:
                    currency_map[currency_id] = {
                        "name": currency.get("name", ""),
                        "symbol": currency.get("symbol", ""),
                    }

        prepared_quotes = []
        for quote in quotes:
            partner = quote.get("partner_id")
            customer = ""
            if isinstance(partner, (list, tuple)) and len(partner) >= 2:
                customer = partner[1]

            activity_type = quote.get("activity_type_id")
            activity_name = ""
            if isinstance(activity_type, (list, tuple)) and len(activity_type) >= 2:
                activity_name = activity_type[1]

            activity_summary = quote.get("activity_summary") or ""
            activity_value = activity_summary or activity_name

            quote_tags = [
                tag_map.get(tag_id, str(tag_id))
                for tag_id in quote.get("tag_ids") or []
            ]

            currency_info = quote.get("currency_id")
            currency_label = ""
            if isinstance(currency_info, (list, tuple)) and currency_info:
                currency_id = currency_info[0]
                currency_details = currency_map.get(currency_id, {})
                currency_label = (
                    currency_details.get("symbol")
                    or currency_details.get("name")
                    or (currency_info[1] if len(currency_info) >= 2 else "")
                )

            amount_total = quote.get("amount_total") or 0
            if currency_label:
                total_display = f"{currency_label}{amount_total:,.2f}"
            else:
                total_display = f"{amount_total:,.2f}"

            prepared_quotes.append(
                {
                    "name": quote.get("name", ""),
                    "customer": customer,
                    "activity": activity_value,
                    "tags": quote_tags,
                    "create_date": _parse_datetime(quote.get("create_date")),
                    "total": amount_total,
                    "total_display": total_display,
                }
            )

        context["quotes"] = prepared_quotes

        products = profile.execute(
            "product.product",
            "search_read",
            fields=["name", "default_code", "write_date", "create_date"],
            limit=10,
            order="write_date desc, create_date desc",
        )
        context["recent_products"] = [
            {
                "name": product.get("name", ""),
                "default_code": product.get("default_code", ""),
                "create_date": _parse_datetime(product.get("create_date")),
                "write_date": _parse_datetime(product.get("write_date")),
            }
            for product in products
        ]

        modules = profile.execute(
            "ir.module.module",
            "search_read",
            [[("state", "=", "installed")]],
            fields=["name", "shortdesc", "latest_version", "author"],
            order="name asc",
        )
        context["installed_modules"] = [
            {
                "name": module.get("name", ""),
                "shortdesc": module.get("shortdesc", ""),
                "latest_version": module.get("latest_version", ""),
                "author": module.get("author", ""),
            }
            for module in modules
        ]

    except Exception:
        logger.exception(
            "Failed to build Odoo quote report for user %s (profile_id=%s)",
            getattr(request.user, "pk", None),
            getattr(profile, "pk", None),
        )
        context["error"] = _("Unable to generate the quote report from Odoo.")
        return TemplateResponse(
            request,
            "admin/core/odoo_quote_report.html",
            context,
            status=502,
        )

    return TemplateResponse(request, "admin/core/odoo_quote_report.html", context)


@api_login_required
def product_list(request):
    """Return a JSON list of products."""

    products = list(
        OdooProduct.objects.values("id", "name", "description", "renewal_period")
    )
    return JsonResponse({"products": products})


@api_login_required
def live_subscription_list(request):
    """Return live subscriptions for the given account_id."""

    account_id = request.GET.get("account_id")
    if not account_id:
        return JsonResponse({"detail": "account_id required"}, status=400)

    try:
        account = CustomerAccount.objects.select_related(
            "live_subscription_product"
        ).get(id=account_id)
    except CustomerAccount.DoesNotExist:
        return JsonResponse({"detail": "invalid account"}, status=404)

    subs = []
    product = account.live_subscription_product
    if product:
        next_renewal = account.live_subscription_next_renewal
        if not next_renewal and account.live_subscription_start_date:
            next_renewal = account.live_subscription_start_date + timedelta(
                days=product.renewal_period
            )

        subs.append(
            {
                "id": account.id,
                "product__name": product.name,
                "next_renewal": next_renewal,
            }
        )

    return JsonResponse({"live_subscriptions": subs})


@csrf_exempt
@api_login_required
def add_live_subscription(request):
    """Create a live subscription for a customer account from POSTed JSON."""

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
        product = OdooProduct.objects.get(id=product_id)
    except OdooProduct.DoesNotExist:
        return JsonResponse({"detail": "invalid product"}, status=404)

    try:
        account = CustomerAccount.objects.get(id=account_id)
    except CustomerAccount.DoesNotExist:
        return JsonResponse({"detail": "invalid account"}, status=404)

    start_date = timezone.now().date()
    account.live_subscription_product = product
    account.live_subscription_start_date = start_date
    account.live_subscription_next_renewal = start_date + timedelta(
        days=product.renewal_period
    )
    account.save()

    return JsonResponse({"id": account.id})
