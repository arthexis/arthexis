from __future__ import annotations

import json
import logging
from datetime import timedelta

from django.contrib.admin.sites import site as admin_site
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt

from apps.core.services.odoo_quote_report import (
    OdooQuoteReportError,
    OdooQuoteReportParams,
    assemble_odoo_quote_report_data,
    build_odoo_quote_report_context_data,
)
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

    try:
        params = OdooQuoteReportParams.from_request(request)
    except ValidationError as exc:
        context["error"] = "; ".join(exc.messages)
        return TemplateResponse(
            request,
            "admin/core/odoo_quote_report.html",
            context,
            status=400,
        )

    try:
        report_data = assemble_odoo_quote_report_data(profile, params=params)
    except OdooQuoteReportError:
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

    context.update(build_odoo_quote_report_context_data(report_data))
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
