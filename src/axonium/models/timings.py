from dataclasses import dataclass
from typing import Any, Dict

@dataclass
class Timings:
    cache_n: int
    prompt_n: int
    prompt_ms: float
    prompt_per_token_ms: float
    prompt_per_second: float
    predicted_n: int
    predicted_ms: float
    predicted_per_token_ms: float
    predicted_per_second: float

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Timings":
        return cls(
            cache_n=data.get("cache_n", 0),
            prompt_n=data.get("prompt_n", 0),
            prompt_ms=data.get("prompt_ms", 0.0),
            prompt_per_token_ms=data.get("prompt_per_token_ms", 0.0),
            prompt_per_second=data.get("prompt_per_second", 0.0),
            predicted_n=data.get("predicted_n", 0),
            predicted_ms=data.get("predicted_ms", 0.0),
            predicted_per_token_ms=data.get("predicted_per_token_ms", 0.0),
            predicted_per_second=data.get("predicted_per_second", 0.0),
        )
