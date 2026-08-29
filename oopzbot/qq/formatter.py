"""QQ-only output normalization."""

from __future__ import annotations

import re

_OOPZ_INLINE_IMAGE_RE = re.compile(
    r"(?m)^[ \t]*!\[IMAGEw\d+h\d+\]\([^\r\n]*\)[ \t]*(?:\r?\n|$)"
)


def plain_text(content: str) -> str:
    """Remove OOPZ attachment markers that QQ cannot render."""

    return _OOPZ_INLINE_IMAGE_RE.sub("", str(content or "")).strip()
