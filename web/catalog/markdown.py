"""Markdown rendering for sensor-design documentation.

Renders the ``SensorDesign.body`` source to HTML. Fenced code blocks tagged
``mermaid`` are emitted as ``<pre class="mermaid">…</pre>`` so mermaid.js (loaded
on the detail page) renders them client-side; all other fences are highlighted
as plain code.

Trust boundary: catalog content is authored only by trusted superusers/admins
via the Django admin, so the rendered HTML is marked safe. It is never fed with
untrusted input.
"""

import html

import markdown
from django.utils.safestring import mark_safe
from pymdownx.superfences import fence_code_format


def _mermaid_fence(source, language, css_class, options, md, **kwargs):
    """Custom SuperFences formatter: emit a mermaid target block."""
    return f'<pre class="mermaid">{html.escape(source)}</pre>'


_EXTENSIONS = [
    "pymdownx.superfences",
    "tables",
    "toc",
    "sane_lists",
    "attr_list",
]

_EXTENSION_CONFIGS = {
    "pymdownx.superfences": {
        "custom_fences": [
            {"name": "mermaid", "class": "mermaid", "format": _mermaid_fence},
            {"name": "*", "class": "", "format": fence_code_format},
        ]
    }
}


def render_markdown(text: str):
    """Render Markdown (with mermaid fences) to safe HTML."""
    md = markdown.Markdown(
        extensions=_EXTENSIONS, extension_configs=_EXTENSION_CONFIGS, output_format="html5"
    )
    return mark_safe(md.convert(text or ""))
