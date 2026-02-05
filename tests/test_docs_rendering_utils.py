from __future__ import annotations

import pytest

from apps.docs import rendering

pytestmark = pytest.mark.critical


def test_render_plain_text_document_escapes_html():
    html, toc = rendering.render_plain_text_document("<script>alert('xss')</script>")

    assert toc == ""
    assert "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;" in html
