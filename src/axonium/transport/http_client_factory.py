from typing import Dict, Mapping, Optional
import httpx
import logging

from ..config.settings import get_sdk_settings

logger = logging.getLogger("llm.sdk.transport.http_client_factory")

class HttpClientFactory:

    @classmethod
    def _default_headers(cls, extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
        settings = get_sdk_settings()
        headers = {
            "Accept": settings.identity.accept,
            "User-Agent": settings.identity.user_agent,
        }
        if extra:
            headers.update(extra)
        return headers


    @classmethod
    def create(
        cls,
        timeout: float,
        extra_headers: dict = None,
    ) -> httpx.Client:

        headers = cls._default_headers(extra_headers)

        logger.debug("Creando httpx.Client común para LLM")

        return httpx.Client(
            timeout=timeout,
            headers=headers,
        )
