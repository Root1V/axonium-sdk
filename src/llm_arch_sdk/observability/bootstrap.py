import logging
import os

logger = logging.getLogger("llm.sdk.observability.langfuse")
from ..config.settings import _sdk_settings
from .helpers import apply_masking


_langfuse_client = None


def _mask_for_langfuse(data):
    """Aplica masking si está habilitado, sino retorna data sin cambios"""
    if not _sdk_settings.observability.enabled:
        return data
    return apply_masking(data, _sdk_settings.observability.masking_strategies)


def get_langfuse_client():
    global _langfuse_client

    if _langfuse_client is not None:
        return _langfuse_client

    # Configurar OpenTelemetry service.name antes de importar Langfuse
    if not os.getenv("OTEL_SERVICE_NAME"):
        os.environ["OTEL_SERVICE_NAME"] = _sdk_settings.otel.service_name

    try:
        from langfuse import Langfuse
    except Exception as exc:
        logger.warning("Langfuse not installed: %s", exc)
        return None

    public_key = _sdk_settings.langfuse.public_key
    secret_key = _sdk_settings.langfuse.secret_key
    host = _sdk_settings.langfuse.base_url
    environment = _sdk_settings.langfuse.environment
    release = _sdk_settings.langfuse.release

    if not public_key or not secret_key or not host:
        logger.info("Langfuse disabled (missing env vars)")
        return None

    try:
        _langfuse_client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
            environment=environment,
            release=release,
            mask=_mask_for_langfuse,
        )
        
        logger.info(
            "Langfuse client initialized [env=%s, release=%s, service=%s, masking=%s]",
            environment,
            release,
            _sdk_settings.otel.service_name,
            _sdk_settings.observability.enabled
        )

    except Exception as exc:
        logger.error("Failed to init Langfuse client: %s", exc)
        _langfuse_client = None

    return _langfuse_client
