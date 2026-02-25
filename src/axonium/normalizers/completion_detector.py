import re
from typing import List

class CompletionDetector:
    """
    Detecta si una respuesta es semánticamente completa,
    incluso si se cortó por límite de tokens.
    """

    # Preguntas o frases de cierre conversacional
    CLOSING_PATTERNS: List[str] = [
        # Preguntas directas al usuario
        r"¿te interesa\b.*\?$",
        r"¿tienes alguna pregunta\b.*\?$",
        r"¿deseas\b.*\?$",
        r"¿quieres\b.*\?$",
        r"¿en qué más puedo ayudar\b.*\?$",
        r"¿te gustaría\b.*\?$",

        # Frases de oferta de ayuda (aunque no terminen en ?)
        r"si necesitas más información",
        r"si tienes alguna pregunta",
        r"estoy aquí para ayudar",
        r"puedo ayudarte con",
        r"dime si necesitas",

        # Cierres conversacionales genéricos
        r"quedo atento",
        r"avísame si",
    ]

    # Señales claras de texto cortado o incompleto
    INCOMPLETE_PATTERNS: List[str] = [
        # Frases truncadas
        r"\b(y|o|porque|como|es|son|la|el)$",

        # Listas o markdown rotos
        r"^[-*]\s*$",
        r"\*\*$",
        r"###\s*$",

        # Enumeraciones incompletas
        r"\b1\.$",
        r"\b2\.$",
        r"\b3\.$",

        # Frases abiertas típicas
        r"la mejor estrategia.*es la$",
        r"puede definirse como$",
    ]

    @staticmethod
    def is_semantically_complete(text: str) -> bool:
        if not text:
            return False

        cleaned = text.strip().lower()

        # 1️ Si parece cortado → NO completo
        for pattern in CompletionDetector.INCOMPLETE_PATTERNS:
            if re.search(pattern, cleaned):
                return False

        # 2️ Si contiene señales claras de cierre → COMPLETO
        for pattern in CompletionDetector.CLOSING_PATTERNS:
            if re.search(pattern, cleaned):
                return True

        # 3️ Fallback: puntuación fuerte al final
        if cleaned.endswith((".", "?", "!")):
            return True

        return False
