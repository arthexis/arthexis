# Website App

Displays the README for a particular app depending on the subdomain.
The mapping uses Django's `Site` model: set a site's *name* to the
label of the app whose README should be shown. If the domain isn't
recognized, the project README is rendered instead.

Rendered pages use [Bootstrap](https://getbootstrap.com/) loaded from a CDN so
the README content has simple default styling. The JavaScript bundle is also
included so interactive components like the navigation dropdowns work. A button
in the upper-right corner toggles between light and dark themes and remembers
the preference using `localStorage`.

When visiting the default *website* domain, a navigation bar shows links to all
enabled apps that expose public URLs. Views decorated with `footer_link` are
collected into a footer where links can be grouped into columns. The
automatically generated sitemap now appears there. The footer stays at the
bottom of short pages but scrolls into view on longer ones.
