# Mailer App

Allows creation of email templates and queuing of emails using those templates.
Queued emails are stored using django-post-office. Use its `send_queued` helper
or the `send_queued_mail` management command to process the queue. A `/purge/`
endpoint deletes sent entries from the queue.
