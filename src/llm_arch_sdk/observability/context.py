from typing import Dict, Optional, Any
import logging

from .bootstrap import get_langfuse_client
from ..config.settings import _sdk_settings


logger = logging.getLogger("llm.sdk.observability.context")

class ObservabilityContext:
    """
    Wrapper simple para update_current_trace de Langfuse.
    
    IMPORTANTE: No genera session_id automáticamente.
    El consumidor debe usar propagate_attributes() al inicio del flujo.
    """

    def __init__(self):
        self._client = get_langfuse_client()

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
                kwargs["user_id"] = _sdk_settings.llm.username

            # Pasar todo directamente a Langfuse
            self._client.update_current_trace(**kwargs)

        except Exception as exc:
            logger.debug("Langfuse update_current_trace failed: %s", exc)


# singleton liviano
obs = ObservabilityContext()


