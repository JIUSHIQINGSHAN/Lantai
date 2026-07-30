from abc import ABC, abstractmethod
from remembrance.models.tables import RawDocument

class SourceAdapter(ABC):
    kind: str = "base"
    @abstractmethod
    def fetch(self, config: dict) -> list[RawDocument]: ...
