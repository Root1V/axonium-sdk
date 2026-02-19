import re
from functools import lru_cache
from typing import Any

from llm_guard.input_scanners import Anonymize
from llm_guard.input_scanners.anonymize_helpers import BERT_LARGE_NER_CONF
from llm_guard.vault import Vault


@lru_cache(maxsize=1)
def _pii_scanner():
    vault = Vault()
    return Anonymize(
        vault,
        recognizer_conf=BERT_LARGE_NER_CONF,
        language="en"
    )


def mask_langfuse_payload(data: Any, **kwargs) -> Any:
    if isinstance(data, str):
        data = mask_secrets(data)
        data = mask_credit_cards(data)
        data = mask_email_and_phone(data)
        data = mask_pii(data)
        return data

    if isinstance(data, dict):
        return {k: mask_langfuse_payload(v) for k, v in data.items()}

    if isinstance(data, list):
        return [mask_langfuse_payload(v) for v in data]

    return data

def mask_credit_cards(text: str) -> str:
    return re.sub(r"\b(?:\d[ -]*?){13,19}\b", "[REDACTED CREDIT CARD]", text)


def mask_email_and_phone(text: str) -> str:
    text = re.sub(r"\b[\w\.-]+@[\w\.-]+\.\w+\b", "[REDACTED EMAIL]", text)
    text = re.sub(
        r"\b(?:\+?\d{1,3}[-.\s]?)?(?:\d{3}[-.\s]?){2}\d{4}\b",
        "[REDACTED PHONE]",
        text,
    )
    return text


def mask_pii(text: str) -> str:
    scanner = _pii_scanner()
    sanitized, _, _ = scanner.scan(text)
    return sanitized
