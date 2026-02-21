from typing import Dict, Optional, Any
import logging

from .bootstrap import get_langfuse_client
from ..config.settings import get_sdk_settings


logger = logging.getLogger("llm.sdk.observability.context")

class ObservabilityContext:
    """
    Wrapper simple para update_current_trace de Langfuse.
    
    IMPORTANTE: No genera session_id automáticamente.
    El consumidor debe usar propagate_attributes() al inicio del flujo.
    """

    def __init__(self):
        self._client = get_langfuse_client()
        self._settings = get_sdk_settings()

    def update(self, **kwargs) -> None:
        """
        Actualiza el trace/span actual de Langfuse.
        
        Acepta cualquier parámetro que soporte update_current_trace():
        - user_id, tags, metadata, input, output, etc.
        
        NOTA: NO genera session_id automáticamente. Usa propagate_attributes()
        en tu código para establecer session_id y otros atributos de contexto.
        """
        if not self._client:
            return

        try:
            # Default solo para user_id si no se proporciona
            if "user_id" not in kwargs:
                kwargs["user_id"] = self._settings.llm.username

            # Pasar todo directamente a Langfuse
            self._client.update_current_trace(**kwargs)

        except Exception as exc:
            logger.debug("Langfuse update_current_trace failed: %s", exc)


# singleton liviano
obs = ObservabilityContext()


# -------------------------
# metadata/tags por defecto
# -------------------------

def build_sdk_metadata(
    adapter: Optional[str] = None,
    operation: Optional[str] = None,
    model: Optional[str] = None,
    **extra
) -> Dict[str, Any]:
    """
    Args:
        adapter: Tipo de adapter usado (llama, openai, langchain)
        operation: Tipo de operación (completion, chat, embedding, health)
        model: Modelo LLM usado
        **extra: Metadata adicional específica de la operación
        
    Returns:
        Dict con metadata técnica del SDK + metadata custom
    """
    settings = get_sdk_settings()
    metadata = {
        "sdk.name": settings.identity.name,
        "sdk.version": settings.identity.mversion,
        "llm.base_url": settings.llm.base_url,
        "llm.timeout": settings.transport.timeout_seconds,
    }
    
    if adapter:
        metadata["adapter.type"] = adapter
    
    if operation:
        metadata["operation.type"] = operation
    
    if model:
        metadata["model"] = model
    
    # Merge con metadata adicional del consumidor
    metadata.update(extra)
    
    return metadata


def build_sdk_tags(model: Optional[str] = None, *custom_tags: str) -> list[str]:
    """
    Args:
        model: Modelo LLM usado (para filtrado rápido por modelo)
        *custom_tags: Tags de negocio custom (ej: "high-priority", "production-ready")
        
    Returns:
        Lista con tags de filtrado + tags custom del consumidor
    """
    tags = []
     
    # Modelo para filtrado rápido (ej: comparar gpt-4 vs llama-7b)
    if model:
        tags.append(f"model:{model}")
    
    # Agregar tags de negocio del consumidor
    if custom_tags:
        tags.extend(custom_tags)
    
    return tags
