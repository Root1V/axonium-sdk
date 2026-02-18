import logging
from openai import OpenAI

from .base import BaseLLMAdapter
from ..transport.auth_http_client_factory import AuthHttpClientFactory
from ..config.settings import _sdk_settings

logger = logging.getLogger("llm.sdk.adapters.openai")


class OpenAIAdapter(BaseLLMAdapter):
    """
    Adapter enterprise para instanciar un cliente OpenAI

    Encapsula:
    - TokenManager
    - httpx.Client
    - base_url
    - timeouts
    """

    def __init__(
        self,
        base_url: str = None,
        timeout: float = None,
        **kwargs
    ):
        self.base_url = base_url or _sdk_settings.llm.base_url
        self.timeout = timeout or _sdk_settings.transport.timeout_seconds
        self.client_kwargs = kwargs

        self._validate_config()

        self._openai_client: OpenAI = None
        self._http_client = AuthHttpClientFactory.create(
            timeout=self.timeout,
        )

    # -------------------------
    # Public API
    # -------------------------
    def client(self) -> OpenAI:
        """
        Devuelve una instancia singleton de OpenAI
        completamente configurada.
        """
        if not self._openai_client:
            logger.info("Inicializando cliente OpenAI")
            
            self._openai_client = OpenAI(
                base_url=self.base_url,
                api_key=_sdk_settings.llm.openai_api_key,
                http_client=self._http_client,
                default_headers=AuthHttpClientFactory._default_headers(),
                **self.client_kwargs,
            )

        return self._openai_client

    # -------------------------
    # Validation
    # -------------------------
    def _validate_config(self):
        if not self.base_url:
            raise RuntimeError("LLM_BASE_URL no está configurado")

        if not self.base_url.startswith("http"):
            raise RuntimeError(f"LLM_BASE_URL inválida: {self.base_url}")
