class ContentNormalizer:
    """
    Limpia artefactos comunes de modelos OSS (GPT-OSS, DeepSeek, etc.)
    """

    @staticmethod
    def normalize(text: str) -> str:
        if not text:
            return ""

        cleaned = text.strip()

        # GPT-OSS suele arrancar con markdown roto
        while cleaned.startswith("*"):
            cleaned = cleaned.lstrip("*").strip()

        return cleaned
