from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import user_passes_test


def staff_required(view_func):
    """Decorator requiring logged-in staff members.

    The wrapped view is marked so navigation helpers can hide links from
    non-staff users.
    """

    decorated = staff_member_required(view_func)
    decorated.login_required = True
    decorated.staff_required = True
    return decorated


def security_group_required(*group_names):
    """Decorator requiring membership in specific security groups."""

    required_groups = frozenset(filter(None, group_names))

    def _has_membership(user):
        if not getattr(user, "is_authenticated", False):
            return False
        if not required_groups:
            return True
        if getattr(user, "is_superuser", False):
            return True
        return user.groups.filter(name__in=required_groups).exists()

    def decorator(view_func):
        decorated = user_passes_test(_has_membership)(view_func)
        decorated.login_required = True
        decorated.required_security_groups = required_groups
        return decorated

    return decorator
