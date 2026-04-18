from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import GalleryCategoryForm, GalleryCreditForm, GalleryImageForm, GalleryTraitAssignmentForm, GalleryTraitForm, GalleryUploadForm
from .models import GalleryImage
from .permissions import can_manage_gallery
from .services import create_gallery_image


def _visible_images_for_user(user):
    queryset = GalleryImage.objects.select_related(
        "content_sample",
        "media_file",
        "owner_user",
        "owner_group",
    )
    if can_manage_gallery(user):
        return queryset
    visibility_filter = Q(include_in_public_gallery=True)
    if getattr(user, "is_authenticated", False):
        visibility_filter |= Q(owner_user=user)
        visibility_filter |= Q(owner_group__in=user.groups.all())
    return queryset.filter(visibility_filter).distinct()


def gallery_index(request):
    images = _visible_images_for_user(request.user)
    return render(request, "gallery/index.html", {"images": images})


def gallery_detail(request, slug):
    image = get_object_or_404(
        GalleryImage.objects.select_related(
            "content_sample",
            "media_file",
            "owner_user",
            "owner_group",
        ).prefetch_related(
            "credits", "categories", "trait_values__trait", "trait_values__category"
        ),
        slug=slug,
    )
    if not image.can_view(request.user):
        raise Http404

    can_view_metadata = image.can_view_metadata(request.user)
    image_form = GalleryImageForm(instance=image) if can_manage_gallery(request.user) else None
    trait_form = GalleryTraitAssignmentForm()
    credit_form = GalleryCreditForm()

    if request.method == "POST" and can_manage_gallery(request.user):
        action = request.POST.get("action", "")
        if action == "update-image":
            image_form = GalleryImageForm(request.POST, instance=image)
            if image_form.is_valid():
                image_form.save()
                messages.success(request, "Image updated.")
                return redirect("gallery:detail", slug=image.slug)
        elif action == "add-trait":
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
        elif action == "add-credit":
            credit_form = GalleryCreditForm(request.POST)
            if credit_form.is_valid():
                credit_obj = credit_form.save(commit=False)
                credit_obj.image = image
                credit_obj.save()
                messages.success(request, "Credit added.")
                return redirect("gallery:detail", slug=image.slug)

    context = {
        "image": image,
        "can_view_metadata": can_view_metadata,
        "can_manage": can_manage_gallery(request.user),
        "image_form": image_form,
        "trait_form": trait_form,
        "credit_form": credit_form,
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

        image = create_gallery_image(
            uploaded_file=form.cleaned_data["image"],
            title=form.cleaned_data["title"],
            description=form.cleaned_data.get("description", ""),
            include_in_public_gallery=form.cleaned_data.get("include_in_public_gallery", False),
            create_content_sample=form.cleaned_data.get("create_content_sample", False),
            owner_user=owner_user,
            owner_group=form.cleaned_data.get("owner_group"),
        )
        messages.success(request, "Image uploaded successfully.")
        return redirect("gallery:detail", slug=image.slug)

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
