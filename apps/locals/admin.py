from __future__ import annotations

from django.conf import settings
from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path
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


def favorite_toggle(request, ct_id):
    ct = get_object_or_404(ContentType, pk=ct_id)
    fav = Favorite.objects.filter(user=request.user, content_type=ct).first()
    next_url = _get_safe_next_url(request)
    if request.method == "POST":
        ContentType.objects.clear_cache()
        if fav and request.POST.get("remove"):
            fav.delete()
            clear_user_favorites_cache(request.user)
            return redirect(next_url or "admin:index")
        label = request.POST.get("custom_label", "").strip()
        user_data = request.POST.get("user_data") == "on"
        priority_raw = request.POST.get("priority", "").strip()
        if fav:
            update_fields = []
            if fav.custom_label != label:
                fav.custom_label = label
                update_fields.append("custom_label")
            if fav.user_data != user_data:
                fav.user_data = user_data
                update_fields.append("user_data")
            try:
                priority = int(priority_raw)
            except (TypeError, ValueError):
                priority = fav.priority
            if fav.priority != priority:
                fav.priority = priority
                update_fields.append("priority")
            if update_fields:
                fav.save(update_fields=update_fields)
        else:
            try:
                priority = int(priority_raw)
            except (TypeError, ValueError):
                priority = 0
            try:
                Favorite.objects.create(
                    user=request.user,
                    content_type=ct,
                    custom_label=label,
                    user_data=user_data,
                    priority=priority,
                )
            except IntegrityError:
                fav = Favorite.objects.filter(user=request.user, content_type=ct).first()
                if fav:
                    update_fields = []
                    if fav.custom_label != label:
                        fav.custom_label = label
                        update_fields.append("custom_label")
                    if fav.user_data != user_data:
                        fav.user_data = user_data
                        update_fields.append("user_data")
                    if fav.priority != priority:
                        fav.priority = priority
                        update_fields.append("priority")
                    if update_fields:
                        fav.save(update_fields=update_fields)
        clear_user_favorites_cache(request.user)
        return redirect(next_url or "admin:index")
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
    favorites = (
        Favorite.objects.filter(user=request.user)
        .select_related("content_type")
        .order_by("priority", "pk")
    )
    if request.method == "POST":
        ContentType.objects.clear_cache()
        selected = set(request.POST.getlist("user_data"))
        for fav in favorites:
            update_fields = []
            user_selected = str(fav.pk) in selected
            if fav.user_data != user_selected:
                fav.user_data = user_selected
                update_fields.append("user_data")

            priority_raw = request.POST.get(f"priority_{fav.pk}", "").strip()
            if priority_raw:
                try:
                    priority = int(priority_raw)
                except (TypeError, ValueError):
                    priority = fav.priority
                else:
                    if fav.priority != priority:
                        fav.priority = priority
                        update_fields.append("priority")
            else:
                if fav.priority != 0:
                    fav.priority = 0
                    update_fields.append("priority")

            if update_fields:
                fav.save(update_fields=update_fields)
        clear_user_favorites_cache(request.user)
        return redirect("admin:favorite_list")
    return render(request, "admin/favorite_list.html", {"favorites": favorites})


def favorite_delete(request, pk):
    fav = get_object_or_404(Favorite, pk=pk, user=request.user)
    fav.delete()
    clear_user_favorites_cache(request.user)
    return redirect("admin:favorite_list")


def favorite_clear(request):
    Favorite.objects.filter(user=request.user).delete()
    clear_user_favorites_cache(request.user)
    return redirect("admin:favorite_list")


def patch_admin_favorites() -> None:
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
