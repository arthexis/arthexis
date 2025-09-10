from django.urls import path as django_path


def landing(label=None):
    """Decorator to mark a view as a landing page."""

    def decorator(view):
        view.landing = True
        view.landing_label = label or view.__name__.replace("_", " ").title()
        return view

    return decorator
