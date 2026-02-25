from dataclasses import dataclass
from typing import List, Optional

from .timings import Timings
from .usage import Usage

@dataclass
class ChatMessage:
    role: str
    content: str

    @classmethod
    def from_dict(cls, data: dict) -> "ChatMessage":
        return cls(
            role=data.get("role"),
            content=data.get("content", ""),
        )


@dataclass
class ChatChoice:
    index: int
    finish_reason: Optional[str]
    message: ChatMessage

    @classmethod
    def from_dict(cls, data: dict) -> "ChatChoice":
        return cls(
            index=data.get("index", 0),
            finish_reason=data.get("finish_reason"),
            message=ChatMessage.from_dict(data.get("message", {})),
        )


@dataclass
class ChatCompletionResult:
    id: str
    model: str
    created: int
    choices: List[ChatChoice]
    usage: Optional[Usage] = None
    system_fingerprint: Optional[str] = None
    object: Optional[str] = None
    timings: Optional[Timings] = None

    @classmethod
    def from_dict(cls, data: dict) -> "ChatCompletionResult":

        return cls(
            id=data.get("id"),
            model=data.get("model"),
            created=data.get("created"),
            object=data.get("object"),
            system_fingerprint=data.get("system_fingerprint"),
            choices=[
                ChatChoice.from_dict(c)
                for c in data.get("choices", [])
            ],
            usage=Usage.from_dict(data["usage"]) if "usage" in data else None,
            timings=Timings.from_dict(data["timings"]) if "timings" in data else None,
        )
