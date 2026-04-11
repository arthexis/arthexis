import secrets

from django.contrib import admin, messages
from django.contrib.auth import login
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.locals.user_data import (
    UserDatumAdminMixin,
    delete_user_fixture,
    dump_user_fixture,
    resolve_fixture_user,
    user_allows_user_data,
)
from apps.core.admin.mixins import OwnedObjectLinksMixin
from apps.core.impersonation import (
    get_impersonator_user_id,
    store_impersonator_user_id,
)
from apps.core.models import get_owned_objects_for_user
from apps.users import temp_passwords
from apps.users.models import User

from .forms import (
    UserChangeRFIDForm,
    UserCreationWithExpirationForm,
    UserRFIDWriteForm,
)
from .inlines import USER_PROFILE_INLINES, UserPhoneNumberInline
from .site import (
    _append_operate_as,
    _include_require_2fa,
    _include_site_template,
    _include_site_template_add,
    _include_temporary_expiration,
)

GUEST_NAME_ADJECTIVES = (
    "brisk",
    "calm",
    "clever",
    "daring",
    "eager",
    "gentle",
    "honest",
    "lively",
    "merry",
    "nimble",
)

GUEST_NAME_NOUNS = (
    "badger",
    "heron",
    "lynx",
    "otter",
    "panda",
    "panther",
    "sparrow",
    "terrapin",
    "whale",
    "wren",
)


@admin.register(User)
class UserAdmin(OwnedObjectLinksMixin, UserDatumAdminMixin, DjangoUserAdmin):
    form = UserChangeRFIDForm
    add_form = UserCreationWithExpirationForm
    actions = (DjangoUserAdmin.actions or []) + [
        "impersonate_selected_user",
        "login_as_guest_user",
    ]
    changelist_actions = ["login_as_guest_user"]
    list_display = (*DjangoUserAdmin.list_display, "impersonate_link")
    fieldsets = _include_site_template(
        _include_temporary_expiration(
            _include_require_2fa(_append_operate_as(DjangoUserAdmin.fieldsets))
        )
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "temporary_expires_at",
                    "password1",
                    "password2",
                ),
            },
        ),
    )
    add_fieldsets = _include_site_template_add(
        _include_temporary_expiration(
            _include_require_2fa(_append_operate_as(add_fieldsets))
        )
    )
    inlines = USER_PROFILE_INLINES + [UserPhoneNumberInline]
    change_form_template = "admin/user_profile_change_form.html"
    _skip_entity_user_datum = True

    def _generate_guest_username(self) -> str:
        attempts = 0
        candidate = None
        while attempts < 10:
            candidate = f"{secrets.choice(GUEST_NAME_ADJECTIVES)}-{secrets.choice(GUEST_NAME_NOUNS)}"
            if not self.model.objects.filter(username=candidate).exists():
                return candidate
            attempts += 1
        suffix = secrets.token_hex(2)
        return f"{candidate}-{suffix}" if candidate else f"guest-{suffix}"

    def _change_url(self, user_id) -> str:
        opts = self.model._meta
        return reverse(f"admin:{opts.app_label}_{opts.model_name}_change", args=[user_id])

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "login-as-guest/",
                self.admin_site.admin_view(self.login_as_guest_user),
                name="core_user_login_as_guest_user",
            ),
            path(
                "<path:user_id>/impersonate/",
                self.admin_site.admin_view(self.impersonate_user_view),
                name="core_user_impersonate",
            ),
        ]
        custom += [
            path(
                "<path:user_id>/write-login-rfid/",
                self.admin_site.admin_view(self.write_login_rfid),
                name="core_user_write_login_rfid",
            ),
        ]
        return custom + urls

    def _can_impersonate(self, request, target=None) -> bool:
        if not request.user.is_active or not request.user.is_superuser:
            return False
        if target is None:
            return True
        if not getattr(target, "is_active", False):
            return False
        if target.pk == request.user.pk:
            return False
        return True

    def get_changelist_actions(self, request):
        parent = getattr(super(), "get_changelist_actions", None)
        actions = []
        if callable(parent):
            parent_actions = parent(request)
            if parent_actions:
                actions.extend(parent_actions)
        if "login_as_guest_user" not in actions:
            actions.append("login_as_guest_user")
        return actions

    def impersonate_link(self, obj):
        if not getattr(obj, "pk", None):
            return "—"
        url = reverse("admin:core_user_impersonate", args=[obj.pk])
        return format_html('<a class="button" href="{}">{}</a>', url, _("Impersonate"))

    impersonate_link.short_description = _("Impersonate")

    @admin.action(description=_("Login as Guest"), permissions=["add"])
    def login_as_guest_user(self, request, queryset=None):
        if not self.has_add_permission(request):
            raise PermissionDenied

        expires_at = timezone.now() + temp_passwords.DEFAULT_EXPIRATION
        username = self._generate_guest_username()
        guest_user = self.model.objects.create_user(
            username=username,
            password=None,
            is_staff=True,
            is_superuser=False,
            require_2fa=False,
            temporary_expires_at=expires_at,
        )

        temp_password = temp_passwords.generate_password()
        entry = temp_passwords.store_temp_password(
            guest_user.username, temp_password, expires_at=expires_at
        )

        login(request, guest_user, backend="apps.users.backends.TempPasswordBackend")

        expires_display = timezone.localtime(entry.expires_at)
        expires_label = expires_display.strftime("%Y-%m-%d %H:%M %Z")
        self.message_user(
            request,
            _(
                "Logged in as %(username)s with temporary password %(password)s (expires %(expires)s)."
            )
            % {
                "username": guest_user.username,
                "password": temp_password,
                "expires": expires_label,
            },
            messages.WARNING,
        )

        redirect_url = request.GET.get("next") or reverse("admin:index")
        return HttpResponseRedirect(redirect_url)

    login_as_guest_user.label = _("Login as Guest")
    login_as_guest_user.requires_queryset = False

    @admin.action(
        description=_("Impersonate selected user"),
        permissions=["change"],
    )
    def impersonate_selected_user(self, request, queryset):
        if not request.user.is_superuser:
            raise PermissionDenied
        selected_ids = list(queryset.values_list("pk", flat=True)[:2])
        if not selected_ids:
            self.message_user(
                request,
                _("Select one user to impersonate."),
                level=messages.WARNING,
            )
            return None
        if len(selected_ids) > 1 or queryset.count() > 1:
            self.message_user(
                request,
                _("Please select exactly one user to impersonate."),
                level=messages.WARNING,
            )
            return None
        return HttpResponseRedirect(
            reverse("admin:core_user_impersonate", args=[selected_ids[0]])
        )

    def impersonate_user_view(self, request, user_id):
        if not request.user.is_superuser:
            raise PermissionDenied
        target = self.get_object(request, user_id)
        if target is None:
            raise PermissionDenied
        if not self._can_impersonate(request, target):
            self.message_user(
                request,
                _("You cannot impersonate the selected user."),
                level=messages.ERROR,
            )
            return HttpResponseRedirect(
                self._change_url(target.pk)
            )
        impersonator_id = get_impersonator_user_id(request.session)
        if impersonator_id is None:
            impersonator_id = request.user.pk
        login(request, target, backend="apps.users.backends.PasswordOrOTPBackend")
        store_impersonator_user_id(request.session, impersonator_id)
        self.message_user(
            request,
            _("You are now impersonating %(username)s.")
            % {"username": target.get_username()},
            level=messages.WARNING,
        )
        next_url = request.GET.get("next")
        if next_url:
            return HttpResponseRedirect(next_url)
        return HttpResponseRedirect("/")

    def get_fieldsets(self, request, obj=None):
        fieldsets = list(super().get_fieldsets(request, obj))
        if obj is not None and fieldsets:
            name, options = fieldsets[0]
            fields = list(options.get("fields", ()))
            rfid_fields_to_add = (
                "login_rfid",
                "login_rfid_key",
                "login_rfid_block",
                "login_rfid_offset",
                "login_rfid_value",
            )
            for field in rfid_fields_to_add:
                if field not in fields:
                    fields.append(field)
            options = options.copy()
            options["fields"] = tuple(fields)
            fieldsets[0] = (name, options)
        return fieldsets

    def _get_operate_as_profile_template(self):
        opts = self.model._meta
        try:
            return reverse(
                f"{self.admin_site.name}:{opts.app_label}_{opts.model_name}_change",
                args=["__ID__"],
            )
        except NoReverseMatch:
            user_opts = User._meta
            try:
                return reverse(
                    f"{self.admin_site.name}:{user_opts.app_label}_{user_opts.model_name}_change",
                    args=["__ID__"],
                )
            except NoReverseMatch:
                return None

    def render_change_form(
        self, request, context, add=False, change=False, form_url="", obj=None
    ):
        payload = None
        if obj is not None:
            direct, via = get_owned_objects_for_user(obj)
            payload = self._build_owned_object_context(
                direct, via, _("Owned via security group")
            )
        self._attach_owned_objects(context, payload)
        response = super().render_change_form(
            request, context, add=add, change=change, form_url=form_url, obj=obj
        )
        if isinstance(response, dict):
            context_data = response
        else:
            context_data = getattr(response, "context_data", None)
        if context_data is not None:
            context_data["show_user_datum"] = False
            context_data["show_seed_datum"] = False
            context_data["show_save_as_copy"] = False
        operate_as_user = None
        operate_as_template = self._get_operate_as_profile_template()
        operate_as_url = None
        if obj and getattr(obj, "operate_as_id", None):
            try:
                operate_as_user = obj.operate_as
            except User.DoesNotExist:
                operate_as_user = None
            if operate_as_user and operate_as_template:
                operate_as_url = operate_as_template.replace(
                    "__ID__", str(operate_as_user.pk)
                )
        if context_data is not None:
            context_data["operate_as_user"] = operate_as_user
            context_data["operate_as_profile_url_template"] = operate_as_template
            context_data["operate_as_profile_url"] = operate_as_url
            if obj and obj.pk:
                context_data["rfid_write_url"] = reverse(
                    "admin:core_user_write_login_rfid", args=[obj.pk]
                )
                if self._can_impersonate(request, obj):
                    context_data["impersonate_url"] = reverse(
                        "admin:core_user_impersonate",
                        args=[obj.pk],
                    )
            if get_impersonator_user_id(getattr(request, "session", None)) is not None:
                context_data["stop_impersonation_url"] = reverse("stop-impersonation")
        return response

    def write_login_rfid(self, request, user_id):
        user = self.get_object(request, user_id)
        if user is None:
            raise PermissionDenied
        if not self.has_change_permission(request, user):
            raise PermissionDenied

        tag = getattr(user, "login_rfid", None)
        form = UserRFIDWriteForm(request.POST or None)
        write_enabled = all(
            [
                tag,
                user.login_rfid_block is not None,
                user.login_rfid_offset is not None,
                user.login_rfid_value,
            ]
        )
        if request.method == "POST" and form.is_valid():
            if not write_enabled:
                form.add_error(
                    None,
                    _("Configure the login RFID, block, offset, and value before writing."),
                )
            else:
                from apps.cards.reader import write_rfid_cell_value

                key_choice = user.login_rfid_key
                key_value = tag.key_a if key_choice == user.LOGIN_RFID_KEY_A else tag.key_b
                write_result = write_rfid_cell_value(
                    block=user.login_rfid_block,
                    offset=user.login_rfid_offset,
                    value=user.login_rfid_value,
                    key=key_value,
                    key_type=key_choice,
                )
                if write_result.get("error"):
                    form.add_error(None, write_result["error"])
                else:
                    scanned = write_result.get("rfid")
                    if scanned and tag and tag.rfid and scanned.upper() != tag.rfid.upper():
                        form.add_error(
                            None,
                            _(
                                "Scanned RFID %(scanned)s does not match the assigned tag "
                                "%(expected)s."
                            )
                            % {"scanned": scanned, "expected": tag.rfid},
                        )
                    else:
                        self.message_user(
                            request,
                            _("RFID login data written successfully."),
                            level=messages.SUCCESS,
                        )
                        return HttpResponseRedirect(
                            self._change_url(user.pk)
                        )

        context = dict(self.admin_site.each_context(request))
        context.update(
            {
                "title": _("Write RFID login data"),
                "user_obj": user,
                "form": form,
                "login_rfid": tag,
                "write_enabled": write_enabled,
                "login_rfid_key": user.login_rfid_key,
                "login_rfid_block": user.login_rfid_block,
                "login_rfid_offset": user.login_rfid_offset,
                "login_rfid_value": user.login_rfid_value,
                "opts": self.model._meta,
                "original": user,
                "media": self.media + form.media,
            }
        )
        return TemplateResponse(
            request, "admin/core/user/write_login_rfid.html", context
        )

    def get_inline_instances(self, request, obj=None):
        inline_instances = super().get_inline_instances(request, obj)
        if obj and getattr(obj, "is_profile_restricted", False):
            profile_inline_classes = tuple(USER_PROFILE_INLINES)
            inline_instances = [
                inline
                for inline in inline_instances
                if inline.__class__ not in profile_inline_classes
            ]
        return inline_instances

    def _update_profile_fixture(self, instance, owner, *, store: bool) -> None:
        if not getattr(instance, "pk", None):
            return
        manager = getattr(type(instance), "all_objects", None)
        if manager is not None:
            manager.filter(pk=instance.pk).update(is_user_data=store)
        instance.is_user_data = store
        if owner is None:
            owner = getattr(instance, "user", None)
        if owner is None:
            return
        if store:
            dump_user_fixture(instance, owner)
        else:
            delete_user_fixture(instance, owner)

    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        owner = form.instance if isinstance(form.instance, User) else None
        for deleted in getattr(formset, "deleted_objects", []):
            owner_user = getattr(deleted, "user", None) or owner
            self._update_profile_fixture(deleted, owner_user, store=False)
        for inline_form in getattr(formset, "forms", []):
            if not hasattr(inline_form, "cleaned_data"):
                continue
            if inline_form.cleaned_data.get("DELETE"):
                continue
            if "user_datum" not in inline_form.cleaned_data:
                continue
            instance = inline_form.instance
            owner_user = getattr(instance, "user", None) or owner
            should_store = bool(inline_form.cleaned_data.get("user_datum"))
            self._update_profile_fixture(instance, owner_user, store=should_store)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not getattr(obj, "pk", None):
            return
        target_user = resolve_fixture_user(obj, obj)
        allow_user_data = user_allows_user_data(target_user)
        if request.POST.get("_user_datum") == "on":
            type(obj).all_objects.filter(pk=obj.pk).update(is_user_data=False)
            obj.is_user_data = False
            delete_user_fixture(obj, target_user)
            self.message_user(
                request,
                _("User data for user accounts is managed through the profile sections."),
            )
        elif obj.is_user_data:
            type(obj).all_objects.filter(pk=obj.pk).update(is_user_data=False)
            obj.is_user_data = False
            delete_user_fixture(obj, target_user)
