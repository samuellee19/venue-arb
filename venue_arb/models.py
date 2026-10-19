from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class Listing:
    venue: str
    market_id: str
    event_id: str
    category: str | None
    title: str
    rules: str
    yes_label: str
    no_label: str
    yes_token: str | None
    no_token: str | None
    close_time: datetime
    resolution_source: str | None
    raw: dict


@dataclass(frozen=True)
class Level:
    side: Literal["yes", "no"]
    level: int
    price: float  # ASK side: price to BUY this outcome
    size: float
