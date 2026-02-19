import logging
from typing import Any, Dict, List

from langfuse import observe
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from llm_arch_sdk.adapters.sdk_runnable import SDKRunnable

from .base_llm_adapter import BaseLLMAdapter
from ..transport.auth_http_client_factory import AuthHttpClientFactory
from ..config.settings import _sdk_settings
from llm_arch_sdk.adapters.llama_adapter import LlamaAdapter




logger = logging.getLogger("llm.sdk.adapters.langchain")


class LangChainAdapter(BaseLLMAdapter):
    """
    Adapter para LangChain con ChatOpenAI y OpenAIEmbeddings.
    
    Proporciona métodos para:
    - chat: Conversaciones con ChatOpenAI
    - completions: Completions de texto (usando ChatOpenAI internamente)
    - embeddings: Generación de embeddings con OpenAIEmbeddings
    """

    def __init__(
        self,
        runnable = None,
        base_url: str = None,
        timeout: float = None,
        **kwargs
        ):
        
        self._runnable = runnable
        
        self.base_url = base_url or _sdk_settings.llm.base_url
        self.timeout = timeout or _sdk_settings.transport.timeout_seconds
        self.client_kwargs = kwargs
        
        self._validate_config()
        
        self._adapter = LlamaAdapter(
                base_url= self.base_url,
                timeout=self.timeout,
                **self.client_kwargs
        )
        
        self._http_client = AuthHttpClientFactory.create(timeout=self.timeout)
        
        # Cliente para chat y completions
        self._chat_client = ChatOpenAI(
            base_url=self.base_url,
            api_key=_sdk_settings.llm.openai_api_key,
            http_client=self._http_client,
            default_headers=AuthHttpClientFactory._default_headers(),
            **self.client_kwargs
        )
        
        # Cliente para embeddings
        self._embeddings_client = OpenAIEmbeddings(
            base_url=self.base_url,
            api_key=_sdk_settings.llm.openai_api_key,
            http_client=self._http_client,
            default_headers=AuthHttpClientFactory._default_headers(),
        )
        

    @observe(name="adapter.langchain.chat")
    def chat(self,  model: str, messages: List[Dict[str, Any]], **kwargs):
        """
        Ejecuta chat completions usando ChatOpenAI.invoke()
        
        Args:
            model: Nombre del modelo a usar
            messages: Lista de mensajes [{"role": "user", "content": "..."}, ...]
            **kwargs: Parámetros adicionales (temperature, max_tokens, etc)
        
        Returns:
            AIMessage con la respuesta del modelo
        """
        # Convertir mensajes de formato OpenAI a formato LangChain
        lc_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            
            if role == "system":
                lc_messages.append(SystemMessage(content=content))
            elif role == "assistant":
                lc_messages.append(AIMessage(content=content))
            else:  # user
                lc_messages.append(HumanMessage(content=content))
        
        # Invocar ChatOpenAI con los mensajes convertidos
        response = self._chat_client.invoke(lc_messages, **kwargs)
        return response

    @observe(name="adapter.langchain.completions")
    def completions(self,  model: str, prompt: str, **kwargs):
        """
        Ejecuta text completions usando ChatOpenAI.invoke() con un mensaje de usuario.
        
        Args:
            model: Nombre del modelo a usar
            prompt: Texto del prompt
            **kwargs: Parámetros adicionales (temperature, max_tokens, etc)
        
        Returns:
            AIMessage con la respuesta del modelo
        """
        # En LangChain no hay separación entre chat y completions
        # Usamos ChatOpenAI con un simple mensaje de usuario
        response = self._chat_client.invoke([HumanMessage(content=prompt)], **kwargs)
        return response

    @observe(name="adapter.langchain.embeddings")
    def embeddings(self, model: str, input: Any, **kwargs):
        """
        Genera embeddings usando OpenAIEmbeddings.
        
        Args:
            model: Nombre del modelo a usar
            input: Texto o lista de textos para generar embeddings
            **kwargs: Parámetros adicionales
        
        Returns:
            Lista de embeddings (vectores)
        """
        if isinstance(input, str):
            # Un solo texto - usar embed_query
            return self._embeddings_client.embed_query(input)
        elif isinstance(input, list):
            # Múltiples textos - usar embed_documents
            return self._embeddings_client.embed_documents(input)
        else:
            raise ValueError(f"Input debe ser str o list[str], recibido: {type(input)}")

    @observe(name="adapter.langchain.health", capture_input=False, capture_output=False)
    def health(self) -> Dict[str, Any]:
        raise NotImplementedError("Health check no implementado para LangChainAdapter")
    
    @observe(name="adapter.langchain.invoke")
    def invoke(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        return self._runnable.invoke(
            {"messages": messages, **kwargs}
        )
        
    @observe(name="adapter.langchain.invoke.llama")
    def invoke_llama(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        
        self._runnable = SDKRunnable(self._adapter)
        
        return self._runnable.invoke(
            {"messages": messages, **kwargs}
        )
    
    
    def client(self):
        """
        Devuelve el cliente de ChatOpenAI para uso directo.
        
        Returns:
            ChatOpenAI instance configurada
        """
        return self._chat_client
    
    def get_embeddings_client(self):
        """
        Devuelve el cliente de embeddings para uso directo.
        
        Returns:
            OpenAIEmbeddings instance configurada
        """
        return self._embeddings_client
    
    def _validate_config(self):
        if not self.base_url:
            raise RuntimeError("LLM_BASE_URL no está configurado")

        if not self.base_url.startswith("http"):
            raise RuntimeError(f"LLM_BASE_URL inválida: {self.base_url}")

