from importlib import import_module

from django.apps import apps as django_apps

from apps.locals.user_data import EntityModelAdmin

from . import site as site  # noqa: F401
from . import users as users  # noqa: F401
from .mixins import (
    OwnableAdminForm,
    OwnableAdminMixin,
    ProfileAdminMixin,
    SaveBeforeChangeAction,
    _build_credentials_actions,
)

_EAGER_ADMIN_MODULES = (
    ("apps.core", ".admin_notice_admin"),
    ("apps.core", ".usage"),
    ("apps.odoo", ".odoo"),
)

for app_config_name, module_name in _EAGER_ADMIN_MODULES:
    if django_apps.is_installed(app_config_name):
        import_module(module_name, __name__)

_LAZY_EXPORTS = {
    "AdminNoticeAdmin": ".admin_notice_admin",
    "CustomerAccountRFIDForm": ".forms",
    "CustomerAccountRFIDInline": ".inlines",
    "EmailCollectorAdmin": ".emails",
    "EmailCollectorInline": ".inlines",
    "EmailInboxAdmin": ".emails",
    "EmailInboxAdminForm": ".forms",
    "EmailOutboxAdminForm": ".forms",
    "EmailOutboxInlineForm": ".forms",
    "EmailSearchForm": ".emails",
    "GROUP_PROFILE_INLINES": ".inlines",
    "InviteLeadAdmin": ".invites",
    "MaskedPasswordFormMixin": ".credential_forms",  # NOSONAR - export name, not a credential
    "OdooCustomerSearchForm": ".odoo",
    "OdooEmployeeAdmin": ".odoo",
    "OdooEmployeeAdminForm": ".forms",
    "OdooProductAdmin": ".odoo",
    "OdooProductAdminForm": ".forms",
    "PROFILE_MODELS": ".inlines",
    "ProfileFormMixin": ".forms",
    "ProfileInlineFormSet": ".forms",
    "RFIDAdmin": ".rfid",
    "RFIDConfirmImportForm": ".rfid_forms",
    "RFIDExportForm": ".rfid_forms",
    "RFIDForm": ".rfid",
    "RFIDImportForm": ".rfid_forms",
    "RFIDResource": ".rfid",
    "UsageEventAdmin": ".usage",
    "USER_PROFILE_INLINES": ".inlines",
    "UserAdmin": ".users",
    "UserChangeRFIDForm": ".forms",
    "UserCreationWithExpirationForm": ".forms",
    "UserPhoneNumberInline": ".inlines",
    "_append_operate_as": ".site",
    "_build_profile_inline": ".inlines",
    "_include_require_2fa": ".site",
    "_include_site_template": ".site",
    "_include_site_template_add": ".site",
    "_include_temporary_expiration": ".site",
    "changelist_view_with_object_links": ".site",
    "CopyRFIDForm": ".rfid",
    "get_app_list_with_application_priorities": ".site",
    "keep_existing": ".credential_forms",
}

_OPTIONAL_EXPORT_REQUIRED_APPS = {
    "CustomerAccountRFIDForm": "apps.energy",
    "CustomerAccountRFIDInline": "apps.energy",
    "OdooCustomerSearchForm": "apps.odoo",
    "OdooEmployeeAdmin": "apps.odoo",
    "OdooEmployeeAdminForm": "apps.odoo",
    "OdooProductAdmin": "apps.odoo",
    "OdooProductAdminForm": "apps.odoo",
}

_PUBLIC_EXPORTS = (
    "AdminNoticeAdmin",
    "CopyRFIDForm",
    "CustomerAccountRFIDForm",
    "CustomerAccountRFIDInline",
    "EmailCollectorAdmin",
    "EmailCollectorInline",
    "EmailInboxAdmin",
    "EmailInboxAdminForm",
    "EmailOutboxAdminForm",
    "EmailOutboxInlineForm",
    "EmailSearchForm",
    "EntityModelAdmin",
    "GROUP_PROFILE_INLINES",
    "InviteLeadAdmin",
    "MaskedPasswordFormMixin",
    "OdooCustomerSearchForm",
    "OdooEmployeeAdmin",
    "OdooEmployeeAdminForm",
    "OdooProductAdmin",
    "OdooProductAdminForm",
    "OwnableAdminForm",
    "OwnableAdminMixin",
    "PROFILE_MODELS",
    "ProfileAdminMixin",
    "ProfileFormMixin",
    "ProfileInlineFormSet",
    "RFIDAdmin",
    "RFIDConfirmImportForm",
    "RFIDExportForm",
    "RFIDForm",
    "RFIDImportForm",
    "RFIDResource",
    "SaveBeforeChangeAction",
    "UsageEventAdmin",
    "USER_PROFILE_INLINES",
    "UserAdmin",
    "UserChangeRFIDForm",
    "UserCreationWithExpirationForm",
    "UserPhoneNumberInline",
    "_append_operate_as",
    "_build_credentials_actions",
    "_build_profile_inline",
    "_include_require_2fa",
    "_include_site_template",
    "_include_site_template_add",
    "_include_temporary_expiration",
    "changelist_view_with_object_links",
    "get_app_list_with_application_priorities",
    "keep_existing",
)


def _export_is_available(name: str, is_installed=django_apps.is_installed) -> bool:
    required_app = _OPTIONAL_EXPORT_REQUIRED_APPS.get(name)
    return required_app is None or is_installed(required_app)


def _build_all_exports(is_installed=django_apps.is_installed) -> list[str]:
    return [
        name
        for name in _PUBLIC_EXPORTS
        if _export_is_available(name, is_installed=is_installed)
    ]


__all__ = _build_all_exports()


def __getattr__(name: str):
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    required_app = _OPTIONAL_EXPORT_REQUIRED_APPS.get(name)
    if required_app is not None and not django_apps.is_installed(required_app):
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}; "
            f"{required_app} is not installed"
        )

    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
