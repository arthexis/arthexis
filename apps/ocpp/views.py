"""Minimal HTTP boundary; protocol routes are added with the CSMS package."""

from django.http import JsonResponse


def health(request):
    return JsonResponse({"status": "ok", "version": "2.0"})
