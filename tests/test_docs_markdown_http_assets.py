from __future__ import annotations

import re

from apps.docs import rendering


ASSET_HTTP_PATTERN = re.compile(
    r"<(?:img|script|link|audio|video|source|iframe|embed)\b[^>]*(?:src|href|srcset)=[\"']http://",
    re.IGNORECASE,
)


def test_render_markdown_with_toc_strips_http_subresources():
    markdown_text = """
![Alt text](https://example.com/image.png)

<img src="http://example.com/image.png" alt="bad">
<script src="http://example.com/app.js"></script>
<link href="http://example.com/style.css" rel="stylesheet">
<audio src="http://example.com/audio.mp3"></audio>
<video src="http://example.com/video.mp4"></video>
<source src="http://example.com/source.mp4" type="video/mp4">
<iframe src="http://example.com/embed"></iframe>
<embed src="http://example.com/embed"></embed>

[External link](http://example.com)
    """

    html, toc = rendering.render_markdown_with_toc(markdown_text)

    assert toc is not None
    assert not ASSET_HTTP_PATTERN.search(html), html

