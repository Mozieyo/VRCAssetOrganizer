from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from vrc_organizer.models.tag import Tag


@dataclass
class Asset:
    id: int = 0
    filename: str = ""
    filepath: Path = field(default_factory=Path)
    filetype: str = "other"
    file_size: int = 0
    mod_time: float = 0.0
    date_added: float = 0.0
    thumbnail: Optional[Path] = None
    thumb_state: str = "pending"
    notes: str = ""
    scan_state: str = "pending"
    tags: list[Tag] = field(default_factory=list)
