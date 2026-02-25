from dataclasses import dataclass


@dataclass
class Health:
    status: str
    version: str

    @classmethod
    def from_dict(cls, data: dict) -> "Health":
        return cls(
            status=data.get("status", ""),
            version=data.get("version", ""),
        )
