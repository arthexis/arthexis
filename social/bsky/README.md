# bsky

Integration with [Bluesky](https://bsky.app).

Users can register their Bluesky handle with an app password so the
project can publish posts on their behalf.  A domain-wide account may
also be configured using the `BSKY_HANDLE` and `BSKY_APP_PASSWORD`
settings to send posts from the site itself.

An app password can be created from the
[App Passwords](https://bsky.app/settings/app-passwords) section of
your Bluesky account settings. The admin interface validates the
credentials by attempting to log in when the account is added.
