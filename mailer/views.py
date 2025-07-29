import json
from django.core.mail import send_mail
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import EmailTemplate, QueuedEmail


@csrf_exempt
def add_template(request):
    """Create an email template from POSTed JSON."""
    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=400)
    try:
        data = json.loads(request.body.decode())
    except json.JSONDecodeError:
        data = request.POST
    name = data.get("name")
    subject = data.get("subject")
    body = data.get("body")
    if not all([name, subject, body]):
        return JsonResponse({"detail": "name, subject and body required"}, status=400)
    tpl, _ = EmailTemplate.objects.update_or_create(name=name, defaults={"subject": subject, "body": body})
    return JsonResponse({"id": tpl.id})


@csrf_exempt
def queue_email(request):
    """Queue an email to be sent later."""
    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=400)
    try:
        data = json.loads(request.body.decode())
    except json.JSONDecodeError:
        data = request.POST
    to = data.get("to")
    template_id = data.get("template_id")
    context = data.get("context") or {}
    if not to or not template_id:
        return JsonResponse({"detail": "to and template_id required"}, status=400)
    qe = QueuedEmail.objects.create(to=to, template_id=template_id, context=context)
    return JsonResponse({"id": qe.id})


def email_status(request, qid):
    """Return whether a queued email was sent."""
    try:
        qe = QueuedEmail.objects.get(id=qid)
    except QueuedEmail.DoesNotExist:
        return JsonResponse({"detail": "not found"}, status=404)
    return JsonResponse({"sent": qe.sent, "sent_at": qe.sent_at})


@csrf_exempt
def purge(request):
    """Delete all sent queued emails."""
    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=400)
    count, _ = QueuedEmail.objects.filter(sent=True).delete()
    return JsonResponse({"purged": count})


def send_queued():
    """Send all unsent queued emails."""
    for email in QueuedEmail.objects.filter(sent=False).select_related("template"):
        tpl = email.template
        subject = tpl.subject.format(**email.context)
        body = tpl.body.format(**email.context)
        send_mail(subject, body, None, [email.to])
        email.mark_sent()
