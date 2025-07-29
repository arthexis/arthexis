# Website App

Displays the README for a particular app depending on the subdomain.
The mapping uses Django's `Site` model: set a site's *name* to the
label of the app whose README should be shown. If the domain isn't
recognized, the project README is rendered instead.
