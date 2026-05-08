from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RateSample:
    at: datetime
    bytes_downloaded: int
