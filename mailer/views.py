import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from post_office import mail
from post_office.models import Email, STATUS

from .models import EmailTemplate


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
    tpl = EmailTemplate.objects.get(id=template_id)
    subject = tpl.subject.format(**context)
    body = tpl.body.format(**context)
    email = mail.send(recipients=[to], subject=subject, message=body)
    return JsonResponse({"id": email.id})


def email_status(request, qid):
    """Return whether a queued email was sent."""
    try:
        email = Email.objects.get(id=qid)
    except Email.DoesNotExist:
        return JsonResponse({"detail": "not found"}, status=404)
    sent = email.status == STATUS.sent
    sent_at = email.last_updated if sent else None
    return JsonResponse({"sent": sent, "sent_at": sent_at})


@csrf_exempt
def purge(request):
    """Delete all sent queued emails."""
    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=400)
    emails = Email.objects.filter(status=STATUS.sent)
    count = emails.count()
    emails.delete()
    return JsonResponse({"purged": count})
