from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class CardInfo:
    frequency: str
    protocol: str
    uid: str | None = None
    raw_details: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "frequency": self.frequency,
            "protocol": self.protocol,
            "uid": self.uid,
            "raw_details": self.raw_details,
            "timestamp": self.timestamp,
        }


class BaseReader(ABC):
    id: str
    name: str

    @abstractmethod
    def scan(self) -> CardInfo | None:
        pass

    @abstractmethod
    def to_dict(self) -> dict:
        pass

    def close(self):
        pass
