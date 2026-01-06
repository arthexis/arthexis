import logging

from django import forms
from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

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
    form = OdooEmployeeAdminForm
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
    actions = ["register_from_odoo"]
    change_list_template = "admin/core/product/change_list.html"

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

    @admin.action(description="Register from Odoo")
    def register_from_odoo(self, request, queryset=None):  # pragma: no cover - simple redirect
        return HttpResponseRedirect(
            reverse(
                f"admin:{self.opts.app_label}_{self.opts.model_name}_register_from_odoo"
            )
        )

    def _build_register_context(self, request):
        opts = self.model._meta
        context = self.admin_site.each_context(request)
        context.update(
            {
                "opts": opts,
                "title": _("Register from Odoo"),
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
                            existing = self.model.objects.filter(
                                odoo_product__id=odoo_id
                            ).first()
                            if existing:
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
