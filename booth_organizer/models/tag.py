from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Tag:
    id: int = 0
    name: str = ""
    color: str = "#6366f1"
