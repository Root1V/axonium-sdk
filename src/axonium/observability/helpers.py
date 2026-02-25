import secrets
import string
from typing import Any
import uuid

from . import masking

def new_session_id() -> str:
    return f"s-{uuid.uuid4()}"

def new_job_id() -> str:
    return f"j-{uuid.uuid4()}"

def _new_short_name(length: int = 8) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))

def new_user_id() -> str:
    return f"u-{_new_short_name()}"

def new_agent_id() -> str:
    return f"a-{_new_short_name()}"

def apply_masking(payload: Any, enabled: list[str]) -> Any:
    """
    Aplica las funciones de masking indicadas por nombre usando introspección.
    """

    value = payload

    for fn_name in enabled:

        fn = getattr(masking, fn_name, None)

        if not callable(fn):
            continue

        try:
            value = fn(value)
        except Exception:
            # nunca romper observabilidad
            pass

    return value