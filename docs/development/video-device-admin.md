# Video device admin snapshots

The video device change form in the Django admin includes a **LATEST** panel that
summarizes the most recent camera snapshot for the selected device. When the
camera is already saved and no snapshot exists yet, the admin attempts to capture
one automatically so the panel can render a preview and metadata right away.

## Snapshot metadata

The **LATEST** panel displays:

- The capture timestamp.
- The detected resolution.
- The image format.
- A preview of the captured image when available.

Use the **Take Snapshot** button in the panel to refresh the snapshot on demand.

## Ownership

Video devices are ownable, but they do not require an owner. A device with no
user or group set is treated as **Public**, which means it is available for
anyone to use.
