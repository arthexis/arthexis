"""Views for TODO functionality within the release app."""

import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from utils.api import api_login_required

from .models import Todo


@csrf_exempt
@api_login_required
def todo_list(request):
    """List existing todos or create a new one."""

    if request.method == "GET":
        data = [
            {"id": t.id, "text": t.text, "completed": t.completed}
            for t in Todo.objects.all()
        ]
        return JsonResponse({"todos": data})

    if request.method == "POST":
        try:
            payload = json.loads(request.body.decode())
        except json.JSONDecodeError:
            payload = request.POST
        text = payload.get("text")
        if not text:
            return JsonResponse({"detail": "text required"}, status=400)
        todo = Todo.objects.create(text=text)
        return JsonResponse(
            {"id": todo.id, "text": todo.text, "completed": todo.completed},
            status=201,
        )

    return JsonResponse({"detail": "Method not allowed"}, status=405)


@csrf_exempt
@api_login_required
def todo_toggle(request, pk: int):
    """Toggle the completion status of a todo."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=400)

    try:
        todo = Todo.objects.get(pk=pk)
    except Todo.DoesNotExist:
        return JsonResponse({"detail": "not found"}, status=404)

    todo.completed = not todo.completed
    todo.save()
    return JsonResponse({"id": todo.id, "completed": todo.completed})
