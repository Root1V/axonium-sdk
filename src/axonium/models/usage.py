from dataclasses import dataclass


@dataclass
class Usage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    @classmethod
    def from_dict(cls, data: dict) -> "Usage":
        return cls(
            prompt_tokens=data.get("prompt_tokens", 0),
            completion_tokens=data.get("completion_tokens", 0),
            total_tokens=data.get("total_tokens", 0),
        )
