from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class ProductEvent:
    """Product analytics event sent to the product event collector Lambda."""

    distinct_id: str
    event_name: str
    timestamp: int
    properties: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "distinct_id": self.distinct_id,
            "event_name": self.event_name,
            "timestamp": self.timestamp,
        }
        if self.properties is not None:
            payload["properties"] = self.properties
        return payload


@dataclass(frozen=True)
class SendEventsResponse:
    """Successful response from the product event collector Lambda."""

    stored: int
    date: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SendEventsResponse:
        return cls(stored=int(data["stored"]), date=str(data["date"]))
