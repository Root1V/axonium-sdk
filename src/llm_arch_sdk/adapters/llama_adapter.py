import logging

from .base import BaseLLMAdapter
from ..client.llm_client import LlmClient
from ..transport.auth_http_client_factory import AuthHttpClientFactory
from ..config.settings import _sdk_settings

logger = logging.getLogger("llm.sdk.adapters.llama")

class LlamaAdapter(BaseLLMAdapter):
    """
    Adapter enterprise para instanciar un cliente LlamaServer

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

        self._llm_client: LlmClient = None
        self._http_client = AuthHttpClientFactory.create(
            timeout=self.timeout,
        )

    def client(self) -> LlmClient:
        """
        Devuelve un cliente minimalista para inferencia con llama-server
        completamente configurada.
        """
        if not self._llm_client:
            logger.info("Inicializando cliente LLM")
            
            self._llm_client = LlmClient(
                base_url=self.base_url,
                http_client=self._http_client,
                **self.client_kwargs
            )
        return self._llm_client

    def _validate_config(self):
        if not self.base_url:
            raise RuntimeError("LLM_BASE_URL no configurada")