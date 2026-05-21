"""TemplateLoader — stub for backward compat."""

from __future__ import annotations
from typing import Any


class TemplateLoader:
    def load_all(self, *, refresh: bool = False) -> dict[str, Any]:
        return {}
