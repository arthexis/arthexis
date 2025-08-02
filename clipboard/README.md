# Clipboard

Stores clipboard text snippets as `Sample` entries. Use the management command
`sample_clipboard` or the Django admin to capture the current system clipboard
content.

Patterns can be defined with optional `[sigils]` to scan the most recent sample.
Each pattern has a `priority` and the admin action **Scan latest sample**
returns the first matching pattern along with any sigil substitutions.

