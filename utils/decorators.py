from django.contrib.admin.views.decorators import staff_member_required


def staff_required(view_func):
    """Decorator requiring logged-in staff members.

    The wrapped view is marked so navigation helpers can hide links from
    non-staff users.
    """

    decorated = staff_member_required(view_func)
    decorated.login_required = True
    decorated.staff_required = True
    return decorated
