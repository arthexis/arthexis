from functools import wraps

from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import resolve_url


ADMIN_LOGIN_URL_NAME = "admin:login"


def staff_required(view_func):
    """Decorator requiring logged-in staff members.

    The wrapped view is marked so navigation helpers can hide links from
    non-staff users.

    Parameters:
        view_func: The view function that requires staff access.

    Returns:
        Callable: Wrapped view enforcing authentication and staff status.

    Raises:
        PermissionDenied: Raised for authenticated non-staff users.
    """

    @wraps(view_func)
    def decorated(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), resolve_url(ADMIN_LOGIN_URL_NAME))
        if not request.user.is_active or not request.user.is_staff:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    decorated.login_required = True
    decorated.staff_required = True
    return decorated


def security_group_required(*group_names):
    """Decorator requiring membership in specific security groups."""

    required_groups = frozenset(filter(None, group_names))

    def decorator(view_func):
        def _has_membership(user):
            if not getattr(user, "is_authenticated", False):
                return False
            if not required_groups:
                return True
            if getattr(user, "is_superuser", False):
                return True
            if user.groups.filter(name__in=required_groups).exists():
                return True
            raise PermissionDenied

        decorated = user_passes_test(_has_membership)(view_func)
        decorated.login_required = True
        decorated.required_security_groups = required_groups
        return decorated

    return decorator
