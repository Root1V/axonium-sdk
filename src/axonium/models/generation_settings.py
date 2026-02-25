from dataclasses import dataclass
from typing import List

@dataclass
class GenerationSettings:
    seed: int
    temperature: float
    top_k: int
    top_p: float
    min_p: float
    typical_p: float
    repeat_last_n: int
    repeat_penalty: float
    presence_penalty: float
    frequency_penalty: float
    max_tokens: int
    n_predict: int
    stream: bool
    stop: List[str]
    samplers: List[str]
    chat_format: str
    reasoning_format: str
    reasoning_in_content: bool

    @classmethod
    def from_dict(cls, data: dict) -> "GenerationSettings":
        return cls(
            seed=data.get("seed"),
            temperature=data.get("temperature"),
            top_k=data.get("top_k"),
            top_p=data.get("top_p"),
            min_p=data.get("min_p"),
            typical_p=data.get("typical_p"),
            repeat_last_n=data.get("repeat_last_n"),
            repeat_penalty=data.get("repeat_penalty"),
            presence_penalty=data.get("presence_penalty"),
            frequency_penalty=data.get("frequency_penalty"),
            max_tokens=data.get("max_tokens"),
            n_predict=data.get("n_predict"),
            stream=data.get("stream"),
            stop=data.get("stop", []),
            samplers=data.get("samplers", []),
            chat_format=data.get("chat_format"),
            reasoning_format=data.get("reasoning_format"),
            reasoning_in_content=data.get("reasoning_in_content"),
        )
