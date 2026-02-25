from dataclasses import dataclass
from typing import Any, Dict

from .generation_settings import GenerationSettings
from .stop_type import StopType
from .timings import Timings

@dataclass
class LLMResponse:
    index: int
    content: str
    model: str
    stop: bool
    stop_type: StopType

    tokens_predicted: int
    tokens_evaluated: int
    tokens_cached: int

    prompt: str

    generation_settings: GenerationSettings
    timings: Timings
    has_new_line: bool
    truncated: bool

    @property
    def is_complete(self) -> bool:
        return self.stop_type == StopType.EOS

    @property
    def stopped_by_limit(self) -> bool:
        return self.stop_type == StopType.LIMIT

    @property
    def needs_continuation(self) -> bool:
        return self.stopped_by_limit and not self.truncated

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "LLMResponse":
        return LLMResponse(
            index=data["index"],
            content=data["content"],
            model=data["model"],
            stop=data["stop"],
            stop_type=StopType(data["stop_type"]),
            tokens_predicted=data["tokens_predicted"],
            tokens_evaluated=data["tokens_evaluated"],
            tokens_cached=data.get("tokens_cached", 0),
            prompt=data["prompt"],
            has_new_line=data.get("has_new_line", False),
            truncated=data.get("truncated", False),
            generation_settings=GenerationSettings.from_dict(
                data["generation_settings"]
            ),
            timings=Timings.from_dict(data["timings"]),
        )

#compliance y debugging.
# {
#   "prompt": llm_response.prompt,
#   "content": llm_response.content,
#   "model": llm_response.model,
#   "generation_settings": llm_response.generation_settings,
#   "tokens_predicted": llm_response.tokens_predicted,
#   "timings": llm_response.timings
# }

# {
#   "stop_type": "limit",
#   "tokens_predicted": 128,
#   "max_tokens": 128,
#   "continuations": 2
# }
