from django.db import models
import re


class Sample(models.Model):
    """Clipboard text captured with timestamp."""

    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.content[:50] if len(self.content) > 50 else self.content


class Pattern(models.Model):
    """Text mask with optional sigils used to match against ``Sample`` content."""

    mask = models.TextField()
    priority = models.IntegerField(default=0)

    class Meta:
        ordering = ["-priority", "id"]

    SIGIL_RE = re.compile(r"\[(.+?)\]")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.mask

    def match(self, text: str):
        """Return mapping of sigils to matched text if this pattern matches ``text``.

        ``None`` is returned when no match is found. A ``dict`` mapping sigil names
        to their substitutions is returned for successful matches. Sigils are
        defined with square brackets, e.g. ``"Hello [world]"``.
        """

        regex, names = self._compile_regex()
        match = re.search(regex, text, re.DOTALL)
        if not match:
            return None
        groups = match.groups()
        return {name: value for name, value in zip(names, groups)}

    def _compile_regex(self):
        """Compile the mask into a regex pattern and return pattern and sigils."""

        pattern_parts = []
        sigil_names = []
        last_index = 0
        for match in self.SIGIL_RE.finditer(self.mask):
            pattern_parts.append(re.escape(self.mask[last_index : match.start()]))
            sigil_names.append(match.group(1))
            pattern_parts.append("(.*?)")
            last_index = match.end()
        pattern_parts.append(re.escape(self.mask[last_index:]))
        regex = "".join(pattern_parts)
        return regex, sigil_names
