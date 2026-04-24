from django import forms
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from apps.discovery.services import record_discovery_item, start_discovery
from apps.locals.user_data import EntityModelAdmin

from .models import (
    OdooDeployment,
    OdooEmployee,
    OdooProduct,
    OdooQuery,
    OdooQueryVariable,
    OdooSaleFactor,
    OdooSaleFactorProductRule,
    OdooSaleOrderTemplate,
)
from .public_query_features import (
    PUBLIC_QUERY_EXECUTION_RESTRICTION_MESSAGE,
    is_public_query_execution_secure_mode_enabled,
)
from .services import sync_odoo_deployments
from .sync_features import (
    ODOO_SYNC_DEPLOYMENT_DISCOVERY_PARAMETER_KEY,
    is_odoo_sync_integration_enabled,
)


class OdooTemplateSetupImportForm(forms.Form):
    SOURCE_TEMPLATES = "templates"
    SOURCE_PRODUCTS = "products"
    SOURCE_EMPLOYEES = "employees"
    SOURCE_CHOICES = (
        (SOURCE_TEMPLATES, _("Quotation templates")),
        (SOURCE_PRODUCTS, _("Extra products")),
        (SOURCE_EMPLOYEES, _("Employees")),
    )

    source_type = forms.ChoiceField(
        choices=SOURCE_CHOICES,
        label=_("Object type"),
    )
    selected_ids = forms.MultipleChoiceField(
        required=False,
        choices=(),
        widget=forms.CheckboxSelectMultiple,
        label=_("Records to import"),
        help_text=_("Select one or more records to import from Odoo."),
    )

    def __init__(
        self,
        *args,
        source_options: list[tuple[str, str]] | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.fields["selected_ids"].choices = source_options or []


class OdooTemplateSetupCreateForm(forms.Form):
    name_prefix = forms.CharField(
        max_length=120,
        initial="Setup Template",
        label=_("Template name prefix"),
    )
    templates = forms.ModelMultipleChoiceField(
        queryset=OdooSaleOrderTemplate.objects.none(),
        required=True,
        label=_("Templates"),
        help_text=_("Pick at least one local template imported from Odoo."),
        widget=forms.CheckboxSelectMultiple,
    )
    products = forms.ModelMultipleChoiceField(
        queryset=OdooProduct.objects.none(),
        required=False,
        label=_("Products"),
        widget=forms.CheckboxSelectMultiple,
    )
    employees = forms.ModelMultipleChoiceField(
        queryset=OdooEmployee.objects.none(),
        required=False,
        label=_("Employees"),
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["templates"].queryset = OdooSaleOrderTemplate.objects.order_by("name")
        self.fields["products"].queryset = OdooProduct.objects.order_by("name")
        self.fields["employees"].queryset = OdooEmployee.objects.order_by("username")

    @staticmethod
    def _resolve_unique_factor_code(name_prefix: str) -> str:
        base_code = slugify(f"{name_prefix} Products")[:64] or "setup-template-products"
        if not OdooSaleFactor.objects.filter(code=base_code).exists():
            return base_code

        max_base_length = 64 - len("-99")
        trimmed_base = base_code[:max_base_length]
        counter = 2
        while counter < 100:
            candidate = f"{trimmed_base}-{counter}"
            if not OdooSaleFactor.objects.filter(code=candidate).exists():
                return candidate
            counter += 1
        raise ValidationError(_("Unable to generate a unique sale factor code."))

    def clean(self):
        cleaned_data = super().clean()
        name_prefix = (cleaned_data.get("name_prefix") or "").strip() or "Setup Template"
        cleaned_data["name_prefix"] = name_prefix
        cleaned_data["factor_code"] = self._resolve_unique_factor_code(name_prefix)
        return cleaned_data


@admin.register(OdooDeployment)
class OdooDeploymentAdmin(DjangoObjectActions, EntityModelAdmin):
    actions = ["discover_instances"]
    changelist_actions = ["discover_instances"]

    list_display = (
        "name",
        "config_path",
        "base_path",
        "db_name",
        "db_host",
        "db_port",
        "http_port",
        "last_discovered",
    )
    search_fields = (
        "name",
        "config_path",
        "db_name",
        "db_user",
        "db_host",
    )
    readonly_fields = ("last_discovered",)

    fieldsets = (
        (None, {"fields": ("name", "config_path", "base_path", "last_discovered")}),
        (
            _("Database"),
            {
                "fields": (
                    "db_name",
                    "db_filter",
                    "db_host",
                    "db_port",
                    "db_user",
                    "db_password",
                    "admin_password",
                )
            },
        ),
        (
            _("Runtime"),
            {
                "fields": (
                    "addons_path",
                    "data_dir",
                    "logfile",
                    "http_port",
                    "longpolling_port",
                )
            },
        ),
    )

    def get_urls(self):  # pragma: no cover - admin hook
        urls = super().get_urls()
        custom = [
            path(
                "discover/",
                self.admin_site.admin_view(self.discover_instances_view),
                name="odoo_odoodeployment_discover",
            ),
        ]
        return custom + urls

    def _discover_url(self) -> str:
        return reverse("admin:odoo_odoodeployment_discover")

    def discover_instances(
        self, request, queryset=None
    ):  # pragma: no cover - admin action
        return HttpResponseRedirect(self._discover_url())

    discover_instances.label = _("Discover")
    discover_instances.short_description = _("Discover")
    discover_instances.requires_queryset = False
    discover_instances.is_discover_action = True

    def discover_instances_view(self, request):
        opts = self.model._meta
        changelist_url = reverse("admin:odoo_odoodeployment_changelist")
        context = {
            **self.admin_site.each_context(request),
            "opts": opts,
            "title": _("Discover"),
            "changelist_url": changelist_url,
            "action_url": self._discover_url(),
            "result": None,
        }

        if request.method == "POST":
            if not (
                self.has_view_or_change_permission(request)
                or self.has_add_permission(request)
            ):
                raise PermissionDenied
            if not is_odoo_sync_integration_enabled(
                ODOO_SYNC_DEPLOYMENT_DISCOVERY_PARAMETER_KEY,
                default=False,
            ):
                self.message_user(
                    request,
                    _(
                        "Odoo deployment discovery sync is disabled by suite feature toggles."
                    ),
                    level=messages.ERROR,
                )
                return TemplateResponse(
                    request,
                    "admin/odoo/odoodeployment/discover.html",
                    context,
                )
            result = sync_odoo_deployments(scan_filesystem=False)
            discovery = start_discovery(
                _("Discover"),
                request,
                model=self.model,
                metadata={
                    "action": "odoo_deployment_discovery",
                    "found": result.get("found"),
                },
            )
            if discovery:
                for instance in result.get("created_instances", []):
                    record_discovery_item(
                        discovery,
                        obj=instance,
                        label=instance.name,
                        created=True,
                        overwritten=False,
                        data={"config_path": instance.config_path},
                    )
                for instance in result.get("updated_instances", []):
                    record_discovery_item(
                        discovery,
                        obj=instance,
                        label=instance.name,
                        created=False,
                        overwritten=True,
                        data={"config_path": instance.config_path},
                    )
                discovery.metadata = {
                    "action": "odoo_deployment_discovery",
                    "created": result["created"],
                    "updated": result["updated"],
                    "found": result["found"],
                    "errors": result.get("errors") or [],
                }
                discovery.save(update_fields=["metadata"])
            context["result"] = result
            if result["created"] or result["updated"]:
                message = _(
                    "Odoo configuration discovery completed. %(created)s created, %(updated)s updated."
                ) % {"created": result["created"], "updated": result["updated"]}
                self.message_user(
                    request,
                    message,
                    level=messages.SUCCESS,
                )
            if result.get("errors"):
                for error in result["errors"]:
                    self.message_user(request, error, level=messages.WARNING)

        return TemplateResponse(
            request,
            "admin/odoo/odoodeployment/discover.html",
            context,
        )


class OdooQueryVariableInline(admin.TabularInline):
    model = OdooQueryVariable
    extra = 0
    fields = (
        "sort_order",
        "key",
        "label",
        "input_type",
        "default_value",
        "is_required",
        "help_text",
    )


class OdooQueryAdminForm(forms.ModelForm):
    PUBLIC_EXECUTION_POLICY = PUBLIC_QUERY_EXECUTION_RESTRICTION_MESSAGE

    class Meta:
        model = OdooQuery
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        secure_mode_enabled = is_public_query_execution_secure_mode_enabled(
            default=False
        )
        if secure_mode_enabled:
            self.fields["enable_public_view"].help_text = _(
                "Enable only when absolutely needed and reviewed. "
            ) + str(self.PUBLIC_EXECUTION_POLICY)
            return
        self.fields["enable_public_view"].help_text = _(
            "This toggle is currently blocked by policy. "
        ) + str(self.PUBLIC_EXECUTION_POLICY)

    def clean_enable_public_view(self):
        enabled = self.cleaned_data.get("enable_public_view", False)
        if enabled and not is_public_query_execution_secure_mode_enabled(default=False):
            raise forms.ValidationError(
                _(
                    "Cannot enable public query execution while secure-mode feature flag is disabled."
                )
            )
        return enabled


@admin.register(OdooQuery)
class OdooQueryAdmin(EntityModelAdmin):
    form = OdooQueryAdminForm
    list_display = (
        "name",
        "model_name",
        "method",
        "profile",
        "enable_public_view",
        "public_view_slug",
    )
    search_fields = ("name", "model_name", "method")
    list_filter = ("enable_public_view", "method")
    readonly_fields = ("public_view_slug", "created_at", "updated_at")
    inlines = [OdooQueryVariableInline]

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "description",
                    "profile",
                )
            },
        ),
        (
            _("Query"),
            {
                "fields": (
                    "model_name",
                    "method",
                    "kwquery",
                )
            },
        ),
        (
            _("Public View"),
            {
                "fields": (
                    "enable_public_view",
                    "public_view_slug",
                    "public_title",
                    "public_description",
                )
            },
        ),
        (
            _("Metadata"),
            {"fields": ("created_at", "updated_at")},
        ),
    )


class OdooSaleFactorProductRuleInline(admin.TabularInline):
    model = OdooSaleFactorProductRule
    extra = 0
    fields = (
        "name",
        "odoo_product",
        "quantity_mode",
        "fixed_quantity",
        "factor_multiplier",
    )


@admin.register(OdooSaleOrderTemplate)
class OdooSaleOrderTemplateAdmin(EntityModelAdmin):
    changelist_actions = ["setup_templates"]
    remote_selection_limit = 200
    list_display = (
        "name",
        "default_new_customer_language",
        "fallback_new_customer_language",
        "resolve_note_sigils",
        "salesperson",
    )
    search_fields = ("name",)
    list_filter = ("resolve_note_sigils",)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "odoo_template",
                    "salesperson",
                )
            },
        ),
        (
            _("Customer and Notes"),
            {
                "fields": (
                    "note_template",
                    "resolve_note_sigils",
                    "default_new_customer_language",
                    "fallback_new_customer_language",
                )
            },
        ),
    )

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "setup-templates/",
                self.admin_site.admin_view(self.setup_templates_view),
                name="odoo_odoosaleordertemplate_setup_templates",
            ),
            path(
                "setup-templates/create/",
                self.admin_site.admin_view(self.setup_templates_create_view),
                name="odoo_odoosaleordertemplate_setup_templates_create",
            ),
        ]
        return custom + urls

    def _setup_templates_url(self) -> str:
        return reverse("admin:odoo_odoosaleordertemplate_setup_templates")

    def _setup_templates_create_url(self) -> str:
        return reverse("admin:odoo_odoosaleordertemplate_setup_templates_create")

    def get_dashboard_actions(self, request):
        if not self.has_change_permission(request):
            return []
        return ["setup_templates"]

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        links = list(extra_context.get("public_view_links") or [])
        setup_url = self._setup_templates_url()
        if not any(link.get("url") == setup_url for link in links):
            links.append({"label": self.setup_templates.label, "url": setup_url})
        extra_context["public_view_links"] = links
        return super().changelist_view(request, extra_context=extra_context)

    def _verified_profile_or_redirect(self, request):
        profile = getattr(request.user, "odoo_employee", None)
        if not profile or not profile.is_verified:
            self.message_user(
                request,
                _("Configure and verify your Odoo employee before running template setup."),
                level=messages.ERROR,
            )
            return None
        return profile

    def _remote_options(self, profile, source_type: str) -> list[tuple[str, str]]:
        if source_type == OdooTemplateSetupImportForm.SOURCE_TEMPLATES:
            rows = profile.execute(
                "sale.order.template",
                "search_read",
                [[]],
                fields=["id", "name"],
                order="name asc",
                limit=self.remote_selection_limit,
            )
            return [
                (str(row["id"]), row.get("name") or f"Template {row['id']}")
                for row in rows
                if row.get("id")
            ]
        if source_type == OdooTemplateSetupImportForm.SOURCE_PRODUCTS:
            rows = profile.execute(
                "product.product",
                "search_read",
                [[]],
                fields=["id", "name"],
                order="name asc",
                limit=self.remote_selection_limit,
            )
            return [
                (str(row["id"]), row.get("name") or f"Product {row['id']}")
                for row in rows
                if row.get("id")
            ]
        rows = profile.execute(
            "res.users",
            "search_read",
            [[("active", "=", True), ("share", "=", False)]],
            fields=["id", "name", "email", "login", "partner_id"],
            order="name asc",
            limit=self.remote_selection_limit,
        )
        return [
            (
                str(row["id"]),
                row.get("name") or row.get("login") or f"Employee {row['id']}",
            )
            for row in rows
            if row.get("id")
        ]

    def _find_remote_row(
        self, profile, source_type: str, source_id: int
    ) -> dict[str, object] | None:
        model_map = {
            OdooTemplateSetupImportForm.SOURCE_TEMPLATES: "sale.order.template",
            OdooTemplateSetupImportForm.SOURCE_PRODUCTS: "product.product",
            OdooTemplateSetupImportForm.SOURCE_EMPLOYEES: "res.users",
        }
        fields_map = {
            OdooTemplateSetupImportForm.SOURCE_TEMPLATES: ["id", "name", "note"],
            OdooTemplateSetupImportForm.SOURCE_PRODUCTS: ["id", "name", "description_sale"],
            OdooTemplateSetupImportForm.SOURCE_EMPLOYEES: ["id", "name", "email", "login", "partner_id"],
        }
        rows = profile.execute(
            model_map[source_type],
            "search_read",
            [[("id", "=", source_id)]],
            fields=fields_map[source_type],
            limit=1,
        )
        if not rows:
            return None
        return rows[0]

    def _resolve_unique_username(self, base_username: str, odoo_uid: int) -> str:
        user_model = get_user_model()
        user_manager = getattr(user_model, "all_objects", user_model.objects)
        if not user_manager.filter(username=base_username).exists():
            return base_username
        suffix = f"-odoo-{odoo_uid}"
        candidate = f"{base_username}{suffix}"
        counter = 2
        while user_manager.filter(username=candidate).exists():
            candidate = f"{base_username}{suffix}-{counter}"
            counter += 1
        return candidate

    @staticmethod
    def _extract_partner_id(source_row: dict[str, object]) -> int | None:
        partner_data = source_row.get("partner_id")
        if not isinstance(partner_data, (list, tuple)) or not partner_data:
            return None
        try:
            return int(partner_data[0])
        except (TypeError, ValueError):
            return None

    def _import_template(self, source_row: dict[str, object]) -> tuple[OdooSaleOrderTemplate, bool]:
        source_id = int(source_row["id"])
        existing = OdooSaleOrderTemplate.objects.filter(odoo_template__id=source_id).first()
        defaults = {
            "name": str(source_row.get("name") or f"Odoo Template {source_id}"),
            "odoo_template": {"id": source_id, "name": source_row.get("name") or f"Template {source_id}"},
            "note_template": str(source_row.get("note") or ""),
        }
        if existing:
            for key, value in defaults.items():
                setattr(existing, key, value)
            existing.save(update_fields=list(defaults.keys()))
            return existing, False
        return OdooSaleOrderTemplate.objects.create(**defaults), True

    def _import_product(self, source_row: dict[str, object]) -> tuple[OdooProduct, bool]:
        source_id = int(source_row["id"])
        existing = OdooProduct.objects.filter(odoo_product__id=source_id).first()
        defaults = {
            "name": str(source_row.get("name") or f"Odoo Product {source_id}"),
            "description": str(source_row.get("description_sale") or ""),
            "renewal_period": 30,
            "odoo_product": {"id": source_id, "name": source_row.get("name") or f"Product {source_id}"},
        }
        if existing:
            for key, value in defaults.items():
                setattr(existing, key, value)
            existing.save(update_fields=list(defaults.keys()))
            return existing, False
        return OdooProduct.objects.create(**defaults), True

    def _import_employee(self, profile, source_row: dict[str, object]) -> tuple[OdooEmployee, bool]:
        source_id = int(source_row["id"])
        existing = OdooEmployee.objects.filter(
            host=profile.host,
            database=profile.database,
            odoo_uid=source_id,
        ).first()
        login = str(source_row.get("login") or "").strip()
        email = str(source_row.get("email") or "").strip()
        username_base = login or email or f"odoo-user-{source_id}"
        partner_id = self._extract_partner_id(source_row)
        if existing:
            desired_username = login or existing.username
            user = existing.user
            if user is not None:
                if user.username != desired_username:
                    desired_username = self._resolve_unique_username(desired_username, source_id)
                user.username = desired_username
                user.email = email
                user.save(update_fields=["username", "email"])
            existing.email = email
            existing.name = str(source_row.get("name") or existing.name)
            existing.partner_id = partner_id
            existing.save(update_fields=["email", "name", "partner_id"])
            return existing, False

        user_model = get_user_model()
        username = self._resolve_unique_username(username_base, source_id)
        user = user_model.objects.create(username=username, email=email)
        user.set_unusable_password()
        user.save(update_fields=["password"])

        employee = OdooEmployee.objects.create(
            user=user,
            host=profile.host,
            database=profile.database,
            username=login or username,
            password="",
            odoo_uid=source_id,
            name=str(source_row.get("name") or ""),
            email=email,
            partner_id=partner_id,
        )
        return employee, True

    def _import_source_selection(self, profile, source_type: str, selected_ids: list[str]) -> tuple[int, int]:
        created = 0
        updated = 0
        for raw_id in selected_ids:
            try:
                source_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            source_row = self._find_remote_row(profile, source_type, source_id)
            if not source_row:
                continue
            if source_type == OdooTemplateSetupImportForm.SOURCE_TEMPLATES:
                _, was_created = self._import_template(source_row)
            elif source_type == OdooTemplateSetupImportForm.SOURCE_PRODUCTS:
                _, was_created = self._import_product(source_row)
            else:
                _, was_created = self._import_employee(profile, source_row)
            if was_created:
                created += 1
            else:
                updated += 1
        return created, updated

    def setup_templates(self, request, queryset=None):
        return HttpResponseRedirect(self._setup_templates_url())

    setup_templates.label = _("Setup Templates")
    setup_templates.short_description = _("Setup Templates")
    setup_templates.requires_queryset = False
    setup_templates.dashboard_url = "admin:odoo_odoosaleordertemplate_setup_templates"

    def setup_templates_view(self, request):
        if not self.has_change_permission(request):
            raise PermissionDenied
        if not self.has_add_permission(request):
            raise PermissionDenied

        profile = self._verified_profile_or_redirect(request)
        if profile is None:
            return HttpResponseRedirect(
                reverse(f"admin:{self.opts.app_label}_{self.opts.model_name}_changelist")
            )

        source_type = request.POST.get("source_type") or request.GET.get("source_type")
        if source_type not in dict(OdooTemplateSetupImportForm.SOURCE_CHOICES):
            source_type = OdooTemplateSetupImportForm.SOURCE_TEMPLATES

        try:
            options = self._remote_options(profile, source_type)
        except Exception:
            self.message_user(
                request,
                _("Could not fetch Odoo records right now. Please verify your Odoo connection."),
                level=messages.ERROR,
            )
            options = []
        form = OdooTemplateSetupImportForm(
            request.POST or None,
            initial={"source_type": source_type},
            source_options=options,
        )

        if request.method == "POST" and form.is_valid():
            try:
                created, updated = self._import_source_selection(
                    profile,
                    source_type=form.cleaned_data["source_type"],
                    selected_ids=form.cleaned_data["selected_ids"],
                )
            except Exception:
                self.message_user(
                    request,
                    _("Import failed due to an Odoo communication error. Please try again."),
                    level=messages.ERROR,
                )
                return HttpResponseRedirect(
                    f"{self._setup_templates_url()}?source_type={form.cleaned_data['source_type']}"
                )
            self.message_user(
                request,
                _("Imported from Odoo. Created: %(created)s | Updated: %(updated)s")
                % {"created": created, "updated": updated},
                level=messages.SUCCESS,
            )
            return HttpResponseRedirect(
                f"{self._setup_templates_url()}?source_type={form.cleaned_data['source_type']}"
            )

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Setup Templates"),
            "form": form,
            "step_two_url": self._setup_templates_create_url(),
            "changelist_url": reverse(
                f"admin:{self.opts.app_label}_{self.opts.model_name}_changelist"
            ),
        }
        return TemplateResponse(
            request,
            "admin/odoo/odoosaleordertemplate/setup_templates_step1.html",
            context,
        )

    def setup_templates_create_view(self, request):
        if not self.has_change_permission(request):
            raise PermissionDenied
        if not self.has_add_permission(request):
            raise PermissionDenied

        form = OdooTemplateSetupCreateForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            templates = list(form.cleaned_data["templates"])
            products = list(form.cleaned_data["products"])
            employees = list(form.cleaned_data["employees"])
            name_prefix = form.cleaned_data["name_prefix"]
            factor_code = form.cleaned_data["factor_code"]
            primary_employee = employees[0] if employees else None

            with transaction.atomic():
                created_templates: list[OdooSaleOrderTemplate] = []
                for source_template in templates:
                    copied = OdooSaleOrderTemplate.objects.create(
                        name=f"{name_prefix}: {source_template.name}",
                        odoo_template=source_template.odoo_template,
                        note_template=source_template.note_template,
                        resolve_note_sigils=source_template.resolve_note_sigils,
                        default_new_customer_language=source_template.default_new_customer_language,
                        fallback_new_customer_language=source_template.fallback_new_customer_language,
                        salesperson=primary_employee,
                    )
                    created_templates.append(copied)

                created_rules = 0
                if created_templates:
                    factor = OdooSaleFactor.objects.create(
                        name=f"{name_prefix} Products",
                        code=factor_code,
                    )
                    factor.templates.set(created_templates)
                    for product in products:
                        OdooSaleFactorProductRule.objects.create(
                            factor=factor,
                            name=product.name,
                            odoo_product=product.odoo_product,
                        )
                        created_rules += 1

            self.message_user(
                request,
                _(
                    "Template setup completed. Templates created: %(templates)s | Product rules created: %(rules)s"
                )
                % {"templates": len(created_templates), "rules": created_rules},
                level=messages.SUCCESS,
            )
            return HttpResponseRedirect(
                reverse(f"admin:{self.opts.app_label}_{self.opts.model_name}_changelist")
            )

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Setup Templates · Step 2"),
            "form": form,
            "step_one_url": self._setup_templates_url(),
            "changelist_url": reverse(
                f"admin:{self.opts.app_label}_{self.opts.model_name}_changelist"
            ),
        }
        return TemplateResponse(
            request,
            "admin/odoo/odoosaleordertemplate/setup_templates_step2.html",
            context,
        )


@admin.register(OdooSaleFactor)
class OdooSaleFactorAdmin(EntityModelAdmin):
    list_display = ("name", "code")
    search_fields = ("name", "code", "description")
    filter_horizontal = ("templates",)
    inlines = [OdooSaleFactorProductRuleInline]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "code",
                    "description",
                    "templates",
                )
            },
        ),
    )
