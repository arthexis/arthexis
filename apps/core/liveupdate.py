from functools import wraps


def live_update(interval=5):
    """Decorator to mark function-based views for automatic refresh."""

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            setattr(request, "live_update_interval", interval)
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


class LiveUpdateMixin:
    """Mixin to enable automatic refresh for class-based views."""

    live_update_interval = 5

    def dispatch(self, request, *args, **kwargs):
        setattr(request, "live_update_interval", self.live_update_interval)
        return super().dispatch(request, *args, **kwargs)
