from abc import ABC, abstractmethod
from typing import Any


class BaseClient(ABC):
    @abstractmethod
    def _request(self, method: str, endpoint: str, **kwargs) -> Any:
        pass
