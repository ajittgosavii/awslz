"""Shareable design links — encode/decode a design to a compact URL-safe token.

A design is small structured data, so we can round-trip it through the URL
(``?d=<token>``) without any server-side state: zlib-compress the JSON and
base64-url-encode it. Anyone who opens the link loads that exact design.
"""

from __future__ import annotations

import base64
import json
import zlib

from lz_core import LZDesign

_FIELDS = set(LZDesign().to_dict().keys())


def encode_design(design_dict: dict) -> str:
    raw = json.dumps(design_dict, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii")


def decode_design(token: str) -> dict | None:
    """Return a sanitized design dict, or None if the token is invalid."""
    try:
        raw = zlib.decompress(base64.urlsafe_b64decode(token.encode("ascii")))
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        # keep only known fields, then validate by constructing
        clean = {k: v for k, v in data.items() if k in _FIELDS}
        LZDesign(**clean)
        return clean
    except Exception:
        return None
