from __future__ import annotations

from django.conf import settings
from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch
from django.urls import path
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from config.request_utils import is_https_request
from .favorites_cache import clear_user_favorites_cache
from .models import Favorite


def _get_safe_next_url(request):
    """Return a sanitized ``next`` parameter for redirect targets."""

    candidate = (
        request.POST.get("next")
        or request.GET.get("next")
        or request.META.get("HTTP_REFERER")
    )
    if not candidate:
        return None
    candidate = candidate.strip()
    if not candidate:
        return None

    allowed_hosts = {request.get_host()}
    allowed_hosts.update(filter(None, settings.ALLOWED_HOSTS))

    if url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts=allowed_hosts,
        require_https=is_https_request(request),
    ):
        return candidate
    return None


def _parse_priority(raw_value: str, fallback: int) -> int:
    """Return parsed favorite priority, falling back when input is invalid."""

    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return fallback


def _get_content_type_changelist_url(ct: ContentType) -> str | None:
    """Return the admin changelist URL for a content type when available."""

    try:
        return reverse(f"admin:{ct.app_label}_{ct.model}_changelist")
    except NoReverseMatch:
        return None


def _reorder_favorites(user, favorite_pk: int, direction: str) -> None:
    """Move a favorite up or down in sequence by normalizing priorities."""

    favorites = list(
        Favorite.objects.select_for_update()
        .filter(user=user)
        .order_by("priority", "pk")
        .only("pk", "priority")
    )
    index_by_pk = {favorite.pk: idx for idx, favorite in enumerate(favorites)}
    current_index = index_by_pk.get(favorite_pk)
    if current_index is None:
        return

    target_index = current_index - 1 if direction == "up" else current_index + 1
    if target_index < 0 or target_index >= len(favorites):
        return

    favorites[current_index], favorites[target_index] = (
        favorites[target_index],
        favorites[current_index],
    )
    favorites_to_update = []
    for index, favorite in enumerate(favorites):
        if favorite.priority != index:
            favorite.priority = index
            favorites_to_update.append(favorite)
    if favorites_to_update:
        Favorite.objects.bulk_update(favorites_to_update, ["priority"])


def favorite_toggle(request, ct_id):
    """Create, update, or configure a user's favorite model shortcut."""

    ct = get_object_or_404(ContentType, pk=ct_id)
    fav = Favorite.objects.filter(user=request.user, content_type=ct).first()
    next_url = _get_safe_next_url(request)
    changelist_url = _get_content_type_changelist_url(ct)

    if request.method == "GET" and not fav:
        try:
            Favorite.objects.create(
                user=request.user,
                content_type=ct,
                user_data=True,
                is_user_data=True,
            )
        except IntegrityError:
            # A concurrent request may have created this favorite already.
            pass
        clear_user_favorites_cache(request.user)
        return redirect(changelist_url or next_url or "admin:index")

    if request.method == "POST":
        ContentType.objects.clear_cache()
        if fav and request.POST.get("remove"):
            fav.delete()
            clear_user_favorites_cache(request.user)
            return redirect(next_url or changelist_url or "admin:index")
        label = request.POST.get("custom_label", "").strip()
        priority_raw = request.POST.get("priority", "").strip()
        if fav:
            update_fields = []
            if fav.custom_label != label:
                fav.custom_label = label
                update_fields.append("custom_label")
            if not fav.user_data:
                fav.user_data = True
                update_fields.append("user_data")
            if not fav.is_user_data:
                Favorite.all_objects.filter(pk=fav.pk).update(is_user_data=True)
                fav.is_user_data = True
            priority = _parse_priority(priority_raw, fav.priority)
            if fav.priority != priority:
                fav.priority = priority
                update_fields.append("priority")
            if update_fields:
                fav.save(update_fields=update_fields)
        else:
            priority = _parse_priority(priority_raw, 0)
            try:
                Favorite.objects.create(
                    user=request.user,
                    content_type=ct,
                    custom_label=label,
                    user_data=True,
                    is_user_data=True,
                    priority=priority,
                )
            except IntegrityError:
                fav = Favorite.objects.filter(user=request.user, content_type=ct).first()
                if fav:
                    update_fields = []
                    if fav.custom_label != label:
                        fav.custom_label = label
                        update_fields.append("custom_label")
                    if not fav.user_data:
                        fav.user_data = True
                        update_fields.append("user_data")
                    if not fav.is_user_data:
                        Favorite.all_objects.filter(pk=fav.pk).update(is_user_data=True)
                        fav.is_user_data = True
                    if fav.priority != priority:
                        fav.priority = priority
                        update_fields.append("priority")
                    if update_fields:
                        fav.save(update_fields=update_fields)
        clear_user_favorites_cache(request.user)
        return redirect(next_url or changelist_url or "admin:index")
    return render(
        request,
        "admin/favorite_confirm.html",
        {
            "content_type": ct,
            "favorite": fav,
            "next": next_url,
            "initial_label": fav.custom_label if fav else "",
            "initial_priority": fav.priority if fav else 0,
            "is_checked": fav.user_data if fav else True,
        },
    )


def favorite_list(request):
    """Render and process bulk updates for a user's favorites."""

    favorites = (
        Favorite.objects.filter(user=request.user)
        .select_related("content_type")
        .order_by("priority", "pk")
    )
    if request.method == "POST":
        ContentType.objects.clear_cache()
        for fav in favorites:
            update_fields = []
            if not fav.user_data:
                fav.user_data = True
                update_fields.append("user_data")
            if not fav.is_user_data:
                Favorite.all_objects.filter(pk=fav.pk).update(is_user_data=True)
                fav.is_user_data = True

            custom_label = request.POST.get(f"custom_label_{fav.pk}", "").strip()
            if fav.custom_label != custom_label:
                fav.custom_label = custom_label
                update_fields.append("custom_label")

            if update_fields:
                fav.save(update_fields=update_fields)

        move_value = request.POST.get("move", "")
        move_direction, _, move_pk = move_value.partition(":")
        if move_direction in {"up", "down"} and move_pk:
            try:
                parsed_move_pk = int(move_pk)
            except (TypeError, ValueError):
                parsed_move_pk = None
            if parsed_move_pk is not None:
                with transaction.atomic():
                    _reorder_favorites(request.user, parsed_move_pk, move_direction)

        clear_user_favorites_cache(request.user)
        return redirect("admin:favorite_list")
    return render(request, "admin/favorite_list.html", {"favorites": favorites})


def favorite_delete(request, pk):
    """Delete a single favorite entry for the current user."""

    fav = get_object_or_404(Favorite, pk=pk, user=request.user)
    fav.delete()
    clear_user_favorites_cache(request.user)
    return redirect("admin:favorite_list")


def favorite_clear(request):
    """Delete all favorites for the current user."""

    Favorite.objects.filter(user=request.user).delete()
    clear_user_favorites_cache(request.user)
    return redirect("admin:favorite_list")


def patch_admin_favorites() -> None:
    """Register custom admin URLs used by favorites tooling once."""

    if getattr(admin.site, "_favorites_patched", False):
        return

    original_get_urls = admin.site.get_urls

    def get_urls():
        urls = original_get_urls()
        my_urls = [
            path(
                "favorites/<int:ct_id>/",
                admin.site.admin_view(favorite_toggle),
                name="favorite_toggle",
            ),
            path("favorites/", admin.site.admin_view(favorite_list), name="favorite_list"),
            path(
                "favorites/delete/<int:pk>/",
                admin.site.admin_view(favorite_delete),
                name="favorite_delete",
            ),
            path(
                "favorites/clear/",
                admin.site.admin_view(favorite_clear),
                name="favorite_clear",
            ),
        ]
        return my_urls + urls

    admin.site.get_urls = get_urls
    admin.site._favorites_patched = True
