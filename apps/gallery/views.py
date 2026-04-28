from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID, uuid4

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.shop.models import ShopProduct

from .forms import (
    GalleryCategoryForm,
    GalleryCreditForm,
    GalleryImageForm,
    GalleryShareForm,
    GalleryTraitAssignmentForm,
    GalleryTraitForm,
    GalleryUploadForm,
)
from .models import GalleryImage
from .permissions import can_manage_gallery
from .services import create_gallery_image

_STAGED_UPLOAD_MAX_AGE_SECONDS = 60 * 60
_STAGED_UPLOAD_SIGNER = TimestampSigner(salt="gallery-upload")


def _stage_uploaded_image(*, uploaded_file, user_id: int) -> str:
    original_name = Path(uploaded_file.name or "image").name
    staged_path = f"gallery/staged/{user_id}/{uuid4().hex}_{original_name}"
    saved_path = default_storage.save(staged_path, uploaded_file)
    return _STAGED_UPLOAD_SIGNER.sign(saved_path)


def _resolve_staged_upload(*, staged_upload_key: str, user_id: int):
    try:
        staged_path = _STAGED_UPLOAD_SIGNER.unsign(
            staged_upload_key,
            max_age=_STAGED_UPLOAD_MAX_AGE_SECONDS,
        )
    except (BadSignature, SignatureExpired):
        return None
    if not str(staged_path).startswith(f"gallery/staged/{user_id}/"):
        return None
    if not default_storage.exists(staged_path):
        return None
    return staged_path


def _clear_staged_upload(*, staged_path: str | None):
    if staged_path and default_storage.exists(staged_path):
        default_storage.delete(staged_path)


def _visible_images_for_user(user):
    now = timezone.now()
    queryset = GalleryImage.objects.select_related(
        "content_sample",
        "media_file",
        "owner_user",
        "owner_group",
    )
    if can_manage_gallery(user):
        return queryset
    visibility_filter = Q(public_release_at__isnull=False, public_release_at__lte=now)
    if getattr(user, "is_authenticated", False):
        visibility_filter |= Q(owner_user=user)
        visibility_filter |= Q(owner_group__in=user.groups.all())
        visibility_filter |= Q(shared_with_users=user)
    return queryset.filter(visibility_filter).distinct()


def _metadata_visibility_filter_for_user(user):
    if user is None or can_manage_gallery(user):
        return Q()
    if not getattr(user, "is_authenticated", False):
        return Q(pk__in=[])
    return Q(owner_user=user) | Q(owner_group__in=user.groups.all())


def _apply_gallery_search(queryset, search_query: str, *, user=None):
    search_query = search_query.strip()
    if not search_query:
        return queryset

    direct_fields_q = (
        Q(title__icontains=search_query)
        | Q(description__icontains=search_query)
        | Q(media_file__original_name__icontains=search_query)
        | Q(media_file__content_type__icontains=search_query)
        | Q(media_file__source_member__icontains=search_query)
        | Q(media_file__file__icontains=search_query)
    )
    metadata_direct_fields_q = (
        Q(owner_user__username__icontains=search_query)
        | Q(owner_user__email__icontains=search_query)
        | Q(owner_user__first_name__icontains=search_query)
        | Q(owner_user__last_name__icontains=search_query)
        | Q(owner_group__name__icontains=search_query)
        | Q(content_sample__name__icontains=search_query)
        | Q(content_sample__kind__icontains=search_query)
        | Q(content_sample__content__icontains=search_query)
        | Q(content_sample__path__icontains=search_query)
        | Q(content_sample__method__icontains=search_query)
        | Q(content_sample__hash__icontains=search_query)
    )
    metadata_related_fields_q = (
        Q(categories__name__icontains=search_query)
        | Q(categories__slug__icontains=search_query)
        | Q(categories__description__icontains=search_query)
        | Q(credits__display_name__icontains=search_query)
        | Q(credits__artist__icontains=search_query)
        | Q(credits__series__icontains=search_query)
        | Q(credits__era__icontains=search_query)
        | Q(credits__apa_citation__icontains=search_query)
        | Q(credits__contributed_elements__icontains=search_query)
        | Q(credits__excluded_elements__icontains=search_query)
        | Q(credits__link_url__icontains=search_query)
        | Q(trait_values__category__name__icontains=search_query)
        | Q(trait_values__category__slug__icontains=search_query)
        | Q(trait_values__trait__name__icontains=search_query)
        | Q(trait_values__trait__slug__icontains=search_query)
        | Q(trait_values__trait__description__icontains=search_query)
        | Q(trait_values__qualitative_value__icontains=search_query)
    )
    normalized_query = search_query.casefold()
    try:
        direct_fields_q |= Q(id=int(search_query))
    except ValueError:
        pass
    try:
        parsed_uuid = UUID(search_query)
    except ValueError:
        pass
    else:
        direct_fields_q |= Q(slug=parsed_uuid)
        metadata_direct_fields_q |= Q(content_sample__name=parsed_uuid)
    if normalized_query in {"public", "published"}:
        direct_fields_q |= Q(public_release_at__isnull=False, public_release_at__lte=timezone.now())
    if normalized_query == "private":
        direct_fields_q |= Q(public_release_at__isnull=True) | Q(public_release_at__gt=timezone.now())
    try:
        metadata_related_fields_q |= Q(trait_values__float_value=float(search_query))
    except ValueError:
        pass

    direct_match_ids = queryset.filter(direct_fields_q).values_list("id", flat=True)
    metadata_queryset = queryset.filter(_metadata_visibility_filter_for_user(user))
    metadata_direct_match_ids = metadata_queryset.filter(metadata_direct_fields_q).values_list("id", flat=True)
    metadata_related_match_ids = (
        metadata_queryset.filter(metadata_related_fields_q).values_list("id", flat=True).distinct()
    )
    return queryset.filter(
        Q(id__in=direct_match_ids)
        | Q(id__in=metadata_direct_match_ids)
        | Q(id__in=metadata_related_match_ids)
    )


def _gallery_navigation_for_image(*, image: GalleryImage, user, search_query: str):
    queryset = _visible_images_for_user(user)
    if search_query:
        filtered_queryset = _apply_gallery_search(queryset, search_query, user=user)
        if filtered_queryset.filter(pk=image.pk).exists():
            queryset = filtered_queryset
    previous_image = (
        queryset.filter(Q(title__lt=image.title) | Q(title=image.title, id__lt=image.id))
        .order_by("-title", "-id")
        .first()
    )
    next_image = (
        queryset.filter(Q(title__gt=image.title) | Q(title=image.title, id__gt=image.id))
        .order_by("title", "id")
        .first()
    )
    return previous_image, next_image


def _rf_card_store_url_for_image(image: GalleryImage) -> str:
    store_is_setup = ShopProduct.objects.filter(
        is_active=True,
        stock_quantity__gt=0,
        supports_gallery_image_printing=True,
        shop__is_active=True,
    ).exists()
    if not store_is_setup:
        return ""
    return f"{reverse('shop:index')}?gallery_image={image.id}"


def gallery_index(request):
    search_query = (request.GET.get("q") or "").strip()
    images = _apply_gallery_search(_visible_images_for_user(request.user), search_query, user=request.user).order_by(
        "title",
        "id",
    )
    return render(
        request,
        "gallery/index.html",
        {
            "images": images,
            "search_query": search_query,
        },
    )


def gallery_detail(request, slug):
    image = get_object_or_404(
        GalleryImage.objects.select_related(
            "content_sample",
            "media_file",
            "owner_user",
            "owner_group",
        ).prefetch_related(
            "categories",
            "credits",
            "shared_with_users",
            "trait_values__category",
            "trait_values__trait",
        ),
        slug=slug,
    )
    if not image.can_view(request.user):
        raise Http404

    can_view_metadata = image.can_view_metadata(request.user)
    can_share = image.can_share(request.user)
    image_form = GalleryImageForm(instance=image) if can_manage_gallery(request.user) else None
    share_form = GalleryShareForm()
    trait_form = GalleryTraitAssignmentForm()
    credit_form = GalleryCreditForm()
    search_query = (request.GET.get("q") or "").strip()
    previous_image, next_image = _gallery_navigation_for_image(
        image=image,
        user=request.user,
        search_query=search_query,
    )

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "share-image" and can_share:
            share_form = GalleryShareForm(request.POST)
            if share_form.is_valid():
                share_user = share_form.cleaned_data["username"]
                if image.owner_user_id == share_user.pk:
                    share_form.add_error("username", "Image owner already has access.")
                elif share_user in image.shared_with_users.all():
                    share_form.add_error("username", "User already has access.")
                else:
                    image.shared_with_users.add(share_user)
                    messages.success(request, f"Shared with {share_user.username}.")
                    return redirect("gallery:detail", slug=image.slug)
        elif action == "update-image" and can_manage_gallery(request.user):
            image_form = GalleryImageForm(request.POST, instance=image)
            if image_form.is_valid():
                image_form.save()
                messages.success(request, "Image updated.")
                return redirect("gallery:detail", slug=image.slug)
        elif action == "add-trait" and can_manage_gallery(request.user):
            trait_form = GalleryTraitAssignmentForm(request.POST)
            if trait_form.is_valid():
                _, created = image.trait_values.update_or_create(
                    category=trait_form.cleaned_data["category"],
                    trait=trait_form.cleaned_data["trait"],
                    qualitative_value=trait_form.cleaned_data["qualitative_value"],
                    defaults={"float_value": trait_form.cleaned_data["float_value"]},
                )
                messages.success(request, "Trait added." if created else "Trait updated.")
                return redirect("gallery:detail", slug=image.slug)
        elif action == "add-credit" and can_manage_gallery(request.user):
            credit_form = GalleryCreditForm(request.POST)
            if credit_form.is_valid():
                credit_obj = credit_form.save(commit=False)
                credit_obj.image = image
                credit_obj.save()
                messages.success(request, "Credit added.")
                return redirect("gallery:detail", slug=image.slug)
        elif action == "share-image":
            return JsonResponse({"detail": "forbidden"}, status=403)

    context = {
        "image": image,
        "can_view_metadata": can_view_metadata,
        "can_manage": can_manage_gallery(request.user),
        "can_share": can_share,
        "share_form": share_form,
        "image_form": image_form,
        "trait_form": trait_form,
        "credit_form": credit_form,
        "gallery_query_string": f"?{urlencode({'q': search_query})}" if search_query else "",
        "next_image": next_image,
        "previous_image": previous_image,
        "rf_card_store_url": _rf_card_store_url_for_image(image),
        "search_query": search_query,
    }
    return render(request, "gallery/detail.html", context)


def gallery_metadata(request, slug):
    image = get_object_or_404(GalleryImage.objects.prefetch_related("trait_values__trait", "trait_values__category", "categories"), slug=slug)
    if not image.can_view_metadata(request.user):
        return JsonResponse({"detail": "forbidden"}, status=403)

    payload = {
        "slug": str(image.slug),
        "categories": [category.slug for category in image.categories.all()],
        "traits": [
            {
                "category": value.category.slug if value.category else None,
                "trait": value.trait.slug,
                "qualitative": value.qualitative_value,
                "value": value.float_value,
            }
            for value in image.trait_values.all()
        ],
    }
    return JsonResponse(payload)


@login_required
def gallery_upload(request):
    if not can_manage_gallery(request.user):
        return JsonResponse({"detail": "forbidden"}, status=403)

    form = GalleryUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        owner_user = None
        owner_username = form.cleaned_data.get("owner_user")
        if owner_username:
            owner_user = get_user_model().objects.filter(username=owner_username).first()
            if owner_user is None:
                form.add_error("owner_user", "User not found.")
                return render(request, "gallery/upload.html", {"form": form})

        staged_upload_key = form.cleaned_data.get("staged_upload_key") or ""
        staged_path = (
            _resolve_staged_upload(staged_upload_key=staged_upload_key, user_id=request.user.pk) if staged_upload_key else None
        )
        if staged_upload_key and staged_path is None:
            form.add_error("image", "The previously uploaded image has expired or is invalid. Please upload it again.")
            return render(request, "gallery/upload.html", {"form": form})
        uploaded_file = form.cleaned_data.get("image")
        staged_handle = None
        if uploaded_file is None and staged_path:
            staged_handle = default_storage.open(staged_path, mode="rb")
            uploaded_file = File(staged_handle, name=Path(staged_path).name)

        try:
            image = create_gallery_image(
                uploaded_file=uploaded_file,
                title=form.cleaned_data["title"],
                description=form.cleaned_data.get("description", ""),
                public_release_at=form.cleaned_data.get("public_release_at"),
                create_content_sample=form.cleaned_data.get("create_content_sample", False),
                owner_user=owner_user,
                owner_group=form.cleaned_data.get("owner_group"),
            )
        finally:
            if staged_handle is not None:
                staged_handle.close()
        _clear_staged_upload(staged_path=staged_path)
        messages.success(request, "Image uploaded successfully.")
        return redirect("gallery:detail", slug=image.slug)

    if request.method == "POST" and request.FILES.get("image") and "image" not in form.errors:
        previous_staged_upload_key = (request.POST.get("staged_upload_key") or "").strip()
        previous_staged_path = (
            _resolve_staged_upload(staged_upload_key=previous_staged_upload_key, user_id=request.user.pk)
            if previous_staged_upload_key
            else None
        )
        _clear_staged_upload(staged_path=previous_staged_path)
        form.data = form.data.copy()
        form.data["staged_upload_key"] = _stage_uploaded_image(
            uploaded_file=request.FILES["image"],
            user_id=request.user.pk,
        )

    return render(request, "gallery/upload.html", {"form": form})


@login_required
def gallery_taxonomy(request):
    if not can_manage_gallery(request.user):
        return JsonResponse({"detail": "forbidden"}, status=403)

    category_form = GalleryCategoryForm(prefix="category")
    trait_form = GalleryTraitForm(prefix="trait")
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "upsert-category":
            category_form = GalleryCategoryForm(request.POST, prefix="category")
            if category_form.is_valid():
                from .models import GalleryCategory

                GalleryCategory.objects.update_or_create(
                    slug=category_form.cleaned_data["slug"],
                    defaults={
                        "name": category_form.cleaned_data["name"],
                        "description": category_form.cleaned_data["description"],
                    },
                )
                messages.success(request, "Category saved.")
                return redirect("gallery:taxonomy")
        elif action == "upsert-trait":
            trait_form = GalleryTraitForm(request.POST, prefix="trait")
            if trait_form.is_valid():
                from .models import GalleryTrait

                GalleryTrait.objects.update_or_create(
                    slug=trait_form.cleaned_data["slug"],
                    defaults={
                        "name": trait_form.cleaned_data["name"],
                        "description": trait_form.cleaned_data["description"],
                    },
                )
                messages.success(request, "Trait saved.")
                return redirect("gallery:taxonomy")

    from .models import GalleryCategory, GalleryTrait

    context = {
        "category_form": category_form,
        "trait_form": trait_form,
        "categories": GalleryCategory.objects.order_by("name"),
        "traits": GalleryTrait.objects.order_by("name"),
    }
    return render(request, "gallery/taxonomy.html", context)
