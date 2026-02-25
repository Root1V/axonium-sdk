from axonium.normalizers.completion_detector import CompletionDetector
from axonium.normalizers.content_normalizer import ContentNormalizer


class TestCompletionDetector:
    def test_is_semantically_complete_empty_text(self):
        assert CompletionDetector.is_semantically_complete("") is False
        assert CompletionDetector.is_semantically_complete("   ") is False

    def test_is_semantically_complete_incomplete_patterns(self):
        # Frases truncadas
        assert CompletionDetector.is_semantically_complete("La mejor estrategia es la") is False
        assert CompletionDetector.is_semantically_complete("Esto puede definirse como") is False
        assert CompletionDetector.is_semantically_complete("y") is False
        assert CompletionDetector.is_semantically_complete("porque") is False

        # Markdown roto
        assert CompletionDetector.is_semantically_complete("- ") is False
        assert CompletionDetector.is_semantically_complete("**") is False
        assert CompletionDetector.is_semantically_complete("### ") is False

        # Enumeraciones
        assert CompletionDetector.is_semantically_complete("1.") is False
        assert CompletionDetector.is_semantically_complete("2.") is False

    def test_is_semantically_complete_closing_patterns(self):
        # Preguntas al usuario
        assert CompletionDetector.is_semantically_complete("¿Te interesa saber más?") is True
        assert CompletionDetector.is_semantically_complete("¿Tienes alguna pregunta al respecto?") is True
        assert CompletionDetector.is_semantically_complete("¿Deseas que te ayude con algo más?") is True

        # Ofertas de ayuda
        assert CompletionDetector.is_semantically_complete("Si necesitas más información, avísame.") is True
        assert CompletionDetector.is_semantically_complete("Estoy aquí para ayudar.") is True
        assert CompletionDetector.is_semantically_complete("Puedo ayudarte con cualquier duda.") is True

        # Cierres conversacionales
        assert CompletionDetector.is_semantically_complete("Quedo atento a tus comentarios.") is True
        assert CompletionDetector.is_semantically_complete("Avísame si necesitas algo.") is True

    def test_is_semantically_complete_punctuation_fallback(self):
        assert CompletionDetector.is_semantically_complete("Esta es una respuesta completa.") is True
        assert CompletionDetector.is_semantically_complete("¿Entiendes el concepto?") is True
        assert CompletionDetector.is_semantically_complete("¡Claro que sí!") is True

    def test_is_semantically_complete_incomplete_without_strong_signals(self):
        # Texto sin puntuación fuerte y sin señales de cierre
        assert CompletionDetector.is_semantically_complete("Esta es una respuesta") is False
        assert CompletionDetector.is_semantically_complete("El resultado es") is False

    def test_is_semantically_complete_case_insensitive(self):
        assert CompletionDetector.is_semantically_complete("¿QUIERES SABER MÁS?") is True
        assert CompletionDetector.is_semantically_complete("estoy aquí para ayudar") is True


class TestContentNormalizer:
    def test_normalize_empty_text(self):
        assert ContentNormalizer.normalize("") == ""
        assert ContentNormalizer.normalize("   ") == ""

    def test_normalize_no_changes_needed(self):
        text = "Esta es una respuesta normal."
        assert ContentNormalizer.normalize(text) == text

    def test_normalize_remove_leading_asterisks(self):
        # GPT-OSS artefactos comunes
        assert ContentNormalizer.normalize("* Esta es una respuesta") == "Esta es una respuesta"
        assert ContentNormalizer.normalize("** Esta es una respuesta") == "Esta es una respuesta"
        assert ContentNormalizer.normalize("*** Esta es una respuesta") == "Esta es una respuesta"

    def test_normalize_multiple_leading_asterisks(self):
        # Caso donde hay múltiples asteriscos al inicio
        assert ContentNormalizer.normalize("**** Esta es una respuesta") == "Esta es una respuesta"

    def test_normalize_strip_whitespace(self):
        assert ContentNormalizer.normalize("  * Respuesta con espacios  ") == "Respuesta con espacios"

    def test_normalize_only_asterisks(self):
        assert ContentNormalizer.normalize("***") == ""
        assert ContentNormalizer.normalize("*") == ""

    def test_normalize_mixed_content(self):
        # Texto con asteriscos en medio (no al inicio)
        text = "Esta *es* una respuesta con **énfasis**."
        assert ContentNormalizer.normalize(text) == text  # No debería cambiarse

    def test_normalize_real_world_example(self):
        # Ejemplo típico de GPT-OSS
        input_text = "*** Respuesta generada por el modelo\n\nEsta es la explicación completa."
        expected = "Respuesta generada por el modelo\n\nEsta es la explicación completa."
        assert ContentNormalizer.normalize(input_text) == expected