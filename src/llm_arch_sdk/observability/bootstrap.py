import logging
import os

logger = logging.getLogger("llm.sdk.observability.langfuse")
from ..config.settings import get_sdk_settings
from .helpers import apply_masking


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
