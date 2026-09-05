from abc import ABC, abstractmethod

from lantai.models.tables import RawDocument


class SourceAdapter(ABC):
    kind: str = "base"
    @abstractmethod
    def fetch(self, config: dict) -> list[RawDocument]: ...
