from dataclasses import dataclass
from typing import List, Optional

from .timings import Timings
from .generation_settings import GenerationSettings

@dataclass
class CompletionResult:
    index: int
    content: str
    model: str
    stop: bool
    stop_type: Optional[str]
    stopping_word: Optional[str]

    tokens_predicted: int
    tokens_evaluated: int
    tokens_cached: int

    prompt: str
    has_new_line: bool
    truncated: bool

    id_slot: Optional[int] = None
    tokens: Optional[List[int]] = None

    generation_settings: Optional[GenerationSettings] = None
    timings: Optional[Timings] = None

    @classmethod
    def from_dict(cls, data: dict) -> "CompletionResult":
        return cls(
            index=data.get("index"),
            content=data.get("content"),
            model=data.get("model"),
            stop=data.get("stop"),
            stop_type=data.get("stop_type"),
            stopping_word=data.get("stopping_word"),

            tokens_predicted=data.get("tokens_predicted", 0),
            tokens_evaluated=data.get("tokens_evaluated", 0),
            tokens_cached=data.get("tokens_cached", 0),

            prompt=data.get("prompt"),
            has_new_line=data.get("has_new_line", False),
            truncated=data.get("truncated", False),

            id_slot=data.get("id_slot"),
            tokens=data.get("tokens"),

            generation_settings=(
                GenerationSettings.from_dict(data["generation_settings"])
                if "generation_settings" in data else None
            ),
            timings=(
                Timings.from_dict(data["timings"])
                if "timings" in data else None
            ),
        )
