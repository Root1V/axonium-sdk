from enum import Enum

class StopType(str, Enum):
    EOS = "eos"
    LIMIT = "limit"
    STOP_WORD = "stop"

