from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScanResult:
    id: int = 0
    asset_id: int = 0
    entry_name: str = ""
    entry_type: str = "other"
    entry_size: int = 0
