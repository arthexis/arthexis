import logging

from django import forms
from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

from apps.discovery.services import record_discovery_item, start_discovery
from apps.locals.user_data import EntityModelAdmin
from apps.odoo.models import OdooEmployee, OdooProduct

from .forms import OdooEmployeeAdminForm, OdooProductAdminForm
from .mixins import (
    OwnableAdminMixin,
    ProfileAdminMixin,
    SaveBeforeChangeAction,
    _build_credentials_actions,
)

logger = logging.getLogger(__name__)


class OdooCustomerSearchForm(forms.Form):
    name = forms.CharField(required=False, label=_("Name contains"))
    email = forms.CharField(required=False, label=_("Email contains"))
    phone = forms.CharField(required=False, label=_("Phone contains"))
    limit = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=200,
        initial=50,
        label=_("Result limit"),
        help_text=_("Limit the number of Odoo customers returned per search."),
    )


@admin.register(OdooEmployee)
class OdooEmployeeAdmin(
    OwnableAdminMixin, ProfileAdminMixin, SaveBeforeChangeAction, EntityModelAdmin
):
    change_form_template = "django_object_actions/change_form.html"
    ownable_fieldset = ("Owner", {"fields": ("user", "group")})
    form = OdooEmployeeAdminForm
    exclude = ("avatar",)
    list_display = ("owner", "host", "database", "credentials_ok", "verified_on")
    list_filter = ()
    readonly_fields = ("verified_on", "odoo_uid", "name", "email", "partner_id")
    actions = ["verify_credentials"]
    change_actions = ["verify_credentials_action", "my_profile_action"]
    changelist_actions = ["my_profile", "generate_quote_report"]
    fieldsets = (
        ("Owner", {"fields": ("user", "group")}),
        ("Configuration", {"fields": ("host", "database")}),
        ("Credentials", {"fields": ("username", "password")}),
        (
            "Odoo Employee",
            {"fields": ("verified_on", "odoo_uid", "name", "email", "partner_id")},
        ),
    )

    def owner(self, obj):
        return obj.owner_display()

    owner.short_description = "Owner"

    @admin.display(description=_("Credentials OK"), boolean=True)
    def credentials_ok(self, obj):
        return bool(obj.password) and obj.is_verified

    def _verify_credentials(self, request, profile):
        try:
            profile.verify()
            self.message_user(request, f"{profile.owner_display()} verified")
        except Exception as exc:  # pragma: no cover - admin feedback
            self.message_user(
                request, f"{profile.owner_display()}: {exc}", level=messages.ERROR
            )

    def generate_quote_report(self, request, queryset=None):
        return HttpResponseRedirect(reverse("odoo-quote-report"))

    generate_quote_report.label = _("Quote Report")
    generate_quote_report.short_description = _("Quote Report")

    (
        verify_credentials,
        verify_credentials_action,
    ) = _build_credentials_actions("verify_credentials", "_verify_credentials")


@admin.register(OdooProduct)
class OdooProductAdmin(EntityModelAdmin):
    form = OdooProductAdminForm
    actions = ["register_from_odoo", "search_orders_for_selected"]
    change_list_template = "admin/core/product/change_list.html"

    def _selected_odoo_product_ids(self, queryset):
        """Return Odoo product IDs extracted from selected Arthexis products."""

        selected_ids: list[int] = []
        for item in queryset:
            odoo_product = item.odoo_product or {}
            odoo_id = odoo_product.get("id")
            try:
                parsed_odoo_id = int(odoo_id)
            except (TypeError, ValueError):
                continue
            selected_ids.append(parsed_odoo_id)
        return selected_ids

    def _prepare_order_lines(self, profile, selected_ids):
        """Fetch and normalize Odoo order lines that match selected product IDs."""

        return profile.execute(
            "sale.order.line",
            "search_read",
            [[("product_id", "in", selected_ids), ("order_id", "!=", False)]],
            fields=["order_id", "product_id", "name", "product_uom_qty", "price_total"],
            limit=0,
        )

    def _prepare_orders(self, profile, order_ids):
        """Fetch sale orders by id and normalize sorting order."""

        if not order_ids:
            return []
        return profile.execute(
            "sale.order",
            "search_read",
            [[("id", "in", order_ids)]],
            fields=["name", "partner_id", "state", "date_order", "amount_total"],
            order="date_order desc",
            limit=0,
        )

    def _odoo_employee_admin(self):
        return self.admin_site._registry.get(OdooEmployee)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "register-from-odoo/",
                self.admin_site.admin_view(self.register_from_odoo_view),
                name=f"{self.opts.app_label}_{self.opts.model_name}_register_from_odoo",
            )
        ]
        return custom + urls

    @admin.action(description=_("Discover"))
    def register_from_odoo(
        self, request, queryset=None
    ):  # pragma: no cover - simple redirect
        return HttpResponseRedirect(
            reverse(
                f"admin:{self.opts.app_label}_{self.opts.model_name}_register_from_odoo"
            )
        )

    register_from_odoo.is_discover_action = True

    @admin.action(description=_("Search Orders for selected"))
    def search_orders_for_selected(self, request, queryset):
        """Show Odoo sale orders that include at least one selected product."""

        profile = getattr(request.user, "odoo_employee", None)
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Orders containing selected products"),
            "selected_products": list(queryset),
            "orders": [],
            "error": None,
            "has_credentials": bool(profile and profile.is_verified),
        }

        if not profile or not profile.is_verified:
            context["error"] = _(
                "Configure and verify your Odoo employee before searching orders."
            )
            return TemplateResponse(
                request,
                "admin/core/product/search_orders_for_selected.html",
                context,
            )

        selected_ids = self._selected_odoo_product_ids(queryset)
        if not selected_ids:
            context["error"] = _(
                "None of the selected products are linked to an Odoo product ID."
            )
            return TemplateResponse(
                request,
                "admin/core/product/search_orders_for_selected.html",
                context,
            )

        try:
            order_lines = self._prepare_order_lines(profile, selected_ids)
        except Exception:
            logger.exception(
                "Failed to fetch sale order lines for selected Odoo products %s (user_id=%s)",
                selected_ids,
                getattr(request.user, "pk", None),
            )
            context["error"] = _("Unable to fetch matching orders from Odoo.")
            return TemplateResponse(
                request,
                "admin/core/product/search_orders_for_selected.html",
                context,
            )

        order_to_lines: dict[int, list[dict]] = {}
        for line in order_lines:
            order_info = line.get("order_id")
            if not isinstance(order_info, (list, tuple)) or not order_info:
                continue
            order_id = order_info[0]
            if not isinstance(order_id, int):
                continue
            order_to_lines.setdefault(order_id, []).append(line)

        orders = self._prepare_orders(profile, list(order_to_lines.keys()))
        prepared_orders = []
        for order in orders:
            order_id = order.get("id")
            if not isinstance(order_id, int):
                continue
            partner = order.get("partner_id")
            partner_name = ""
            if isinstance(partner, (list, tuple)) and len(partner) > 1:
                partner_name = partner[1]
            prepared_lines = []
            for line in order_to_lines.get(order_id, []):
                product_info = line.get("product_id")
                product_name = ""
                if isinstance(product_info, (list, tuple)) and len(product_info) > 1:
                    product_name = product_info[1]
                prepared_lines.append(
                    {
                        "name": line.get("name") or product_name,
                        "product_name": product_name,
                        "quantity": line.get("product_uom_qty"),
                        "total": line.get("price_total"),
                    }
                )
            prepared_orders.append(
                {
                    "name": order.get("name", ""),
                    "customer": partner_name,
                    "state": order.get("state", ""),
                    "date_order": order.get("date_order"),
                    "amount_total": order.get("amount_total"),
                    "lines": prepared_lines,
                }
            )

        context["orders"] = prepared_orders
        return TemplateResponse(
            request,
            "admin/core/product/search_orders_for_selected.html",
            context,
        )

    def _build_register_context(self, request):
        opts = self.model._meta
        context = self.admin_site.each_context(request)
        context.update(
            {
                "opts": opts,
                "title": _("Discover"),
                "has_credentials": False,
                "profile_url": None,
                "products": [],
                "selected_product_id": request.POST.get("product_id", ""),
            }
        )

        profile_admin = self._odoo_employee_admin()
        if profile_admin is not None:
            context["profile_url"] = profile_admin.get_my_profile_url(request)

        profile = getattr(request.user, "odoo_employee", None)
        if not profile or not profile.is_verified:
            context["credential_error"] = _(
                "Configure your Odoo employee before registering products."
            )
            return context, None

        try:
            products = profile.execute(
                "product.product",
                "search_read",
                fields=[
                    "name",
                    "description_sale",
                    "list_price",
                    "standard_price",
                ],
                limit=0,
            )
        except Exception as exc:
            logger.exception(
                "Failed to fetch Odoo products for user %s (profile_id=%s, host=%s, database=%s)",
                getattr(getattr(request, "user", None), "pk", None),
                getattr(profile, "pk", None),
                getattr(profile, "host", None),
                getattr(profile, "database", None),
            )
            context["error"] = _("Unable to fetch products from Odoo.")
            if getattr(request.user, "is_superuser", False):
                fault = getattr(exc, "faultString", "")
                message = str(exc)
                details = [
                    f"Host: {getattr(profile, 'host', '')}",
                    f"Database: {getattr(profile, 'database', '')}",
                    f"User ID: {getattr(profile, 'odoo_uid', '')}",
                ]
                if fault and fault != message:
                    details.append(f"Fault: {fault}")
                if message:
                    details.append(f"Exception: {type(exc).__name__}: {message}")
                else:
                    details.append(f"Exception type: {type(exc).__name__}")
                context["debug_error"] = "\n".join(details)
            return context, []

        context["has_credentials"] = True
        simplified = []
        for product in products:
            simplified.append(
                {
                    "id": product.get("id"),
                    "name": product.get("name", ""),
                    "description_sale": product.get("description_sale", ""),
                    "list_price": product.get("list_price"),
                    "standard_price": product.get("standard_price"),
                }
            )
        context["products"] = simplified
        return context, simplified

    def register_from_odoo_view(self, request):
        context, products = self._build_register_context(request)
        if products is None:
            return TemplateResponse(
                request, "admin/core/product/register_from_odoo.html", context
            )

        if request.method == "POST" and context.get("has_credentials"):
            if not self.has_add_permission(request):
                context["form_error"] = _("You do not have permission to add products.")
            else:
                product_id = request.POST.get("product_id")
                if not product_id:
                    context["form_error"] = _("Select a product to register.")
                else:
                    try:
                        odoo_id = int(product_id)
                    except (TypeError, ValueError):
                        context["form_error"] = _("Invalid product selection.")
                    else:
                        match = next(
                            (item for item in products if item.get("id") == odoo_id),
                            None,
                        )
                        if not match:
                            context["form_error"] = _(
                                "The selected product was not found. Reload the page and try again."
                            )
                        else:
                            discovery = start_discovery(
                                _("Discover"),
                                request,
                                model=self.model,
                                metadata={"source": "odoo", "odoo_id": odoo_id},
                            )
                            existing = self.model.objects.filter(
                                odoo_product__id=odoo_id
                            ).first()
                            if existing:
                                if discovery:
                                    record_discovery_item(
                                        discovery,
                                        obj=existing,
                                        label=existing.name,
                                        created=False,
                                        overwritten=False,
                                        data={
                                            "source": "odoo",
                                            "odoo_id": odoo_id,
                                        },
                                    )
                                self.message_user(
                                    request,
                                    _(
                                        "Product %(name)s already imported; opening existing record."
                                    )
                                    % {"name": existing.name},
                                    level=messages.WARNING,
                                )
                                return HttpResponseRedirect(
                                    reverse(
                                        "admin:%s_%s_change"
                                        % (
                                            existing._meta.app_label,
                                            existing._meta.model_name,
                                        ),
                                        args=[existing.pk],
                                    )
                                )
                            product = self.model.objects.create(
                                name=match.get("name") or f"Odoo Product {odoo_id}",
                                description=match.get("description_sale", "") or "",
                                renewal_period=30,
                                odoo_product={
                                    "id": odoo_id,
                                    "name": match.get("name", ""),
                                },
                            )
                            if discovery:
                                record_discovery_item(
                                    discovery,
                                    obj=product,
                                    label=product.name,
                                    created=True,
                                    overwritten=False,
                                    data={
                                        "source": "odoo",
                                        "odoo_id": odoo_id,
                                    },
                                )
                            self.log_addition(
                                request, product, "Registered product from Odoo"
                            )
                            self.message_user(
                                request,
                                _("Imported %(name)s from Odoo.")
                                % {"name": product.name},
                            )
                            return HttpResponseRedirect(
                                reverse(
                                    "admin:%s_%s_change"
                                    % (
                                        product._meta.app_label,
                                        product._meta.model_name,
                                    ),
                                    args=[product.pk],
                                )
                            )

        return TemplateResponse(
            request, "admin/core/product/register_from_odoo.html", context
        )
