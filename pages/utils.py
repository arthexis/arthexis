from django.urls import path as django_path


def landing(label=None):
    """Decorator to mark a view as a landing page."""

    def decorator(view):
        view.landing = True
        view.landing_label = label or view.__name__.replace("_", " ").title()
        return view

    return decorator


def landing_leads_supported() -> bool:
    """Return ``True`` when the local node supports landing lead tracking."""

    from nodes.models import Node

    node = Node.get_local()
    if not node:
        return False
    return node.has_feature("celery-queue")
