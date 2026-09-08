"""Compatibility bridge for user-owned admin inlines during core UI extraction."""

from apps.core.admin.inlines import USER_PROFILE_INLINES, UserPhoneNumberInline

__all__ = ["USER_PROFILE_INLINES", "UserPhoneNumberInline"]
