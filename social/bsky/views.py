from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .services import post_from_domain, post_from_user, register_account


@require_POST
@login_required
def register(request):
    handle = request.POST["handle"]
    password = request.POST["app_password"]
    register_account(request.user, handle, password)
    return JsonResponse({"status": "ok"})


@require_POST
@login_required
def post(request):
    text = request.POST["text"]
    post_from_user(request.user, text)
    return JsonResponse({"status": "ok"})


@require_POST
@staff_member_required
def domain_post(request):
    text = request.POST["text"]
    post_from_domain(text)
    return JsonResponse({"status": "ok"})
