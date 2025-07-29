# Mailer App

Allows creation of email templates and queuing of emails using those templates.
Queued emails can be sent with the `send_queued` helper or via a management
command. A `/purge/` endpoint deletes sent entries from the queue.
