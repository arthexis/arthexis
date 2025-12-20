# Favorites Cookbook

Create quick-access shortcuts for frequently used admin models and views.

- [Access points](#access-points)
- [Adding a favorite](#adding-a-favorite)
- [Editing priorities and labels](#editing-priorities-and-labels)
- [Marking favorites as user data](#marking-favorites-as-user-data)
- [Clearing favorites](#clearing-favorites)
- [Troubleshooting](#troubleshooting)

## Access points

- The admin dashboard renders star icons next to every app and model row (`apps/sites/templates/admin/app_list.html` and `apps/sites/templates/admin/includes/dashboard_app_list_content.html`).
- Clicking a star invokes the `admin:favorite_toggle` view defined in [`apps/locals/admin.py`](../../apps/locals/admin.py) and returns you to the current page after saving.
- Use the **Favorites** link in the admin navigation sidebar to open the management list (`apps/locals/templates/admin/favorite_list.html`). The route is registered as `admin:favorite_list` in [`apps/locals/admin.py`](../../apps/locals/admin.py#L98-L135).

## Adding a favorite

1. Visit any admin changelist or dashboard tile that exposes the star icon.
2. Click the icon to open the confirmation dialog (`apps/locals/templates/admin/favorite_confirm.html`).
3. Provide an optional **Custom label** to override the default model name displayed in the favorites list.
4. Set a **Priority** number (lower values appear first). Leave blank to use the default priority of `0`.
5. Choose whether to mark the shortcut as user data (see below).
6. Submit the form to save the `locals.Favorite` instance (`apps/locals/admin.py` lines 34-91).

The confirmation screen persists the `next` query parameter so you return to the original page after saving or removing a favorite.

## Editing priorities and labels

Open the **Favorites** management page to review all shortcuts for the current user. The table lists the target model, custom label, priority, and user data flag. Update multiple entries at once:

1. Adjust the numeric priority inputs (`priority_<pk>` fields) as needed. Empty values reset the priority to `0`.
2. Toggle the checkboxes in the **Mark as User Data** column.
3. Click **Save** to persist the changes. The view iterates through each favorite and updates changed fields (`apps/locals/admin.py` lines 98-135).

Use the **Remove** link on each row to delete a single favorite, or the **Remove all favorites** link to clear the list entirely.

## Marking favorites as user data

Favorites support the same `is_user_data` flag used elsewhere in the platform. When the checkbox is selected:

- The favorite is exported with the rest of the user data fixtures (`apps/locals/models.py` lines 9-25 integrate with the shared `Entity` base class).
- The entry appears in the User Data dashboard described in the [User Data Cookbook](user-data.md).
- Clearing the checkbox removes the JSON fixture on disk the next time the favorite is saved (`apps/locals/admin.py` lines 98-135).

## Clearing favorites

- **Single favorite** – Use the **Remove** link beside the row (`admin:favorite_delete`).
- **All favorites** – Click **Remove all favorites** to call `admin:favorite_clear`, which deletes every `Favorite` belonging to the logged-in user.

Both views live in [`apps/locals/admin.py`](../../apps/locals/admin.py#L136-L147) and redirect back to the favorites list after completion.

## Troubleshooting

- **Star icon does nothing** – Ensure you are authenticated. Anonymous users cannot create favorites because the view expects `request.user`.
- **Favorites list is empty** – Favorites are stored per user. Switch accounts or verify that the `pages_favorite` table contains records for your profile.
- **Priority changes revert** – The view casts priorities to integers; invalid input falls back to the previous value. Enter whole numbers only.
