from django.contrib import messages
from django.shortcuts import redirect, render

from utils.decorators import staff_required

from .forms import MessageForm
from .notifications import notify


@staff_required
def send(request):
    form = MessageForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        msg = form.save()
        notify(msg.subject, msg.body)
        messages.success(request, "Message sent")
        return redirect(request.path)
    return render(request, "msg/send.html", {"form": form})
