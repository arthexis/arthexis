# Admin dashboard performance risks

This document summarizes the elements that can slow down the Django admin
home page (`/admin/`) and push load times beyond two seconds.

## Google Calendar widget
- The sidebar calls the `user_google_calendar` template tag during server
  rendering (see `pages/templates/admin/index.html`, lines 600-640). That tag
  resolves the authenticated user's `GoogleCalendarProfile` and immediately
  fetches events.
- Event retrieval issues an outbound `requests.get` call to the Google Calendar
  API with a 10-second timeout on every page load when credentials are present
  (`core/models.py`, lines 1364-1395). This network call blocks response
  rendering and is the most likely contributor when load times exceed two
  seconds, especially on slow networks or when Google throttles the request.

## Per-model badge counters
- Each model row invokes `badge_counters` to display dynamic badges in the app
  list (`pages/templates/admin/includes/dashboard_model_row.html`,
  lines 22-26).
- On cache misses, the tag performs a `ContentType` lookup followed by a query
  for enabled `BadgeCounter` rows and executes each counter's
  `build_display()` method (`pages/templatetags/admin_extras.py`,
  lines 495-529). With many models or expensive badge callables, this can
  generate dozens of queries and additional processing per request before the
  page renders.

## Dashboard rule evaluation
- The same model rows also call `model_rule_status` for every model
  (`pages/templates/admin/includes/dashboard_model_row.html`, lines 27-33).
- The helper retrieves a matching `DashboardRule` and may invoke a dynamic
  handler when a rule is absent (`pages/templatetags/admin_extras.py`,
  lines 532-579). Each call adds at least one query and can trigger arbitrary
  Python logic. Large app lists magnify the cost and increase overall render
  time.

## Migration history lookups
- The sidebar lists recently updated models via `recent_model_structure_changes`
  (`pages/templates/admin/index.html`, lines 650-674).
- The tag loads the migration graph and scans applied migration operations to
  find structural changes (`pages/templatetags/admin_extras.py`,
  lines 160-232). While usually lightweight, it adds extra database and disk
  access to every dashboard render and can become noticeable on systems with
  extensive migration history.

## Other synchronous database work
- The dashboard pulls the latest `NetMessage` content and recent admin log
  entries during template rendering (`pages/templatetags/admin_extras.py`,
  lines 136-149; `pages/templates/admin/index.html`, lines 675-716). These are
  smaller queries but still contribute to total response time when combined with
  the heavier operations above.

## Summary of bottlenecks
- Network-bound Google Calendar fetches are the highest-risk delay.
- Per-model badge counter and dashboard rule evaluations introduce N+1 query
  patterns tied to the number of registered admin models.
- Additional queries for migration history and recent activity add overhead on
  every page view.

Consider deferring external calls to asynchronous widgets, caching badge and
rule results more aggressively, or gating optional modules behind feature flags
when optimizing `/admin/` load times.
