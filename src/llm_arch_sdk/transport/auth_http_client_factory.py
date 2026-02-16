import httpx
import logging
from ..auth.token_manager import TokenManager
from .http_client_factory import HttpClientFactory
from ..config.settings import _sdk_settings

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
        timeout = timeout or _sdk_settings.transport.timeout_seconds

        headers = cls._default_headers(extra_headers)

        logger.debug("Creando httpx.Client con Autenticacion común para LLM")

        return httpx.Client(
            auth=auth,
            timeout=timeout,
            headers=headers,
        )
