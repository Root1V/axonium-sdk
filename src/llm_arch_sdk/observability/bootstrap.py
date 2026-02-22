import logging
import os

from ..config.settings import get_sdk_settings
from .helpers import apply_masking

logger = logging.getLogger("llm.sdk.observability.langfuse")

_langfuse_client = None


def _mask_for_langfuse(data):
    """Aplica masking si está habilitado, sino retorna data sin cambios"""
    settings = get_sdk_settings()
    if not settings.observability.enabled:
        return data
    return apply_masking(data, settings.observability.masking_strategies)


def get_langfuse_client():
    global _langfuse_client
    settings = get_sdk_settings()
    
    # Si observabilidad está deshabilitada, no inicializar Langfuse
    if not settings.observability.enabled:
        logger.info("Langfuse disabled (observability.enabled=False)")
        return None
    
    s_otel = settings.otel

    if _langfuse_client is not None:
        return _langfuse_client

    # Configurar OpenTelemetry service.name antes de importar Langfuse
    if not os.getenv(s_otel.env_name):
        os.environ[s_otel.env_name] = s_otel.service_name

    try:
        from langfuse import Langfuse
    except Exception as exc:
        logger.warning("Langfuse not installed: %s", exc)
        return None

    s_langfuse = settings.langfuse

    if not s_langfuse.public_key or not s_langfuse.secret_key or not s_langfuse.base_url:
        logger.info("Langfuse disabled (missing env vars)")
        return None

    try:
        _langfuse_client = Langfuse(
            public_key=s_langfuse.public_key,
            secret_key=s_langfuse.secret_key,
            host=s_langfuse.base_url,
            environment=s_langfuse.environment,
            release=s_langfuse.release,
            mask=_mask_for_langfuse,
        )
        
        logger.info(
            "Langfuse client initialized [env=%s, release=%s, service=%s, masking=%s]",
            s_langfuse.environment,
            s_langfuse.release,
            s_otel.service_name,
            settings.observability.enabled
        )

    except Exception as exc:
        logger.error("Failed to init Langfuse client: %s", exc)
        _langfuse_client = None

    return _langfuse_client


# -------------------------
# Decorador condicional
# -------------------------

_observability_warning_shown = False  # Flag para mostrar advertencia solo una vez

def observe(**kwargs):
    """
    Decorador condicional que solo aplica observabilidad si está habilitada.
    
    Si observability.enabled=False, retorna la función sin modificar.
    Si observability.enabled=True, aplica el decorador @observe de Langfuse.
    
    IMPORTANTE: Este decorador se evalúa en tiempo de importación del módulo,
    no en tiempo de ejecución de la función. La configuración de observabilidad
    se lee una vez cuando se importa el módulo.
    """
    settings = get_sdk_settings()
    
    def decorator(func):
        global _observability_warning_shown
        
        # Si observabilidad está deshabilitada, retornar función sin modificar
        if not settings.observability.enabled:
            # Mostrar advertencia INFO solo la primera vez
            if not _observability_warning_shown:
                logger.info(
                    "⚠️  Observability disabled - @observe decorators are no-ops "
                    "(change OBSERVABILITY_ENABLED=True to enable Langfuse tracing)"
                )
                _observability_warning_shown = True
            
            # Log DEBUG para cada función (para debugging detallado)
            logger.debug("Skipping @observe for %s (observability disabled)", func.__name__)
            return func
        
        # Si está habilitada, intentar importar y aplicar decorador de Langfuse
        try:
            from langfuse import observe as langfuse_observe
            return langfuse_observe(**kwargs)(func)
        except ImportError:
            logger.warning("Langfuse not installed, skipping @observe for %s", func.__name__)
            return func
    
    return decorator
