import httpx
import logging
from ..auth.token_manager import TokenManager
from .http_client_factory import HttpClientFactory
from ..config.settings import get_sdk_settings

logger = logging.getLogger("llm.sdk.transport.auth_http_client_factory")

class AuthHttpClientFactory(HttpClientFactory):

    @classmethod
    def create(
        cls,
        auth: TokenManager = None,
        timeout: float = None,
        extra_headers: dict = None,
    ) -> httpx.Client:
            
        auth = auth or TokenManager()
        timeout = timeout or get_sdk_settings().auth.token_timeout

        headers = cls._default_headers(extra_headers)

        logger.debug("Creando httpx.Client con Autenticacion común para LLM")

        return httpx.Client(
            auth=auth,
            timeout=timeout,
            headers=headers,
        )

    @classmethod
    def create_async(
        cls,
        auth: TokenManager = None,
        timeout: float = None,
        extra_headers: dict = None,
    ) -> httpx.AsyncClient:

        auth = auth or TokenManager()
        timeout = timeout or get_sdk_settings().auth.token_timeout

        headers = cls._default_headers(extra_headers)

        logger.debug("Creando httpx.AsyncClient con Autenticacion común para LLM")

        return httpx.AsyncClient(
            auth=auth,
            timeout=timeout,
            headers=headers,
        )
