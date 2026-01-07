# Video Devices

## Ownership

Video devices are ownable, so they can be assigned to a user or security group for access
control. When a device has no owner, it is treated as **Public** and is available for
use without restriction.

## Admin snapshots

In the admin change form for a video device (`/admin/video/videodevice/<id>/change/`),
the **LATEST** section displays the most recent snapshot captured for the device. It
includes the capture timestamp, resolution, and image format, plus a preview image when
available.

If a device has no snapshots yet and the record already exists, the change form will
automatically capture a snapshot and tag it to the device so the **LATEST** section can
populate on first load (provided the local node camera stack is available).

Use the **Take Snapshot** action in the change form to refresh the snapshot on demand.
