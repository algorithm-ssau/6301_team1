import os

# Будущие варианты: OpenAI, YandexGPT, GigaChat, локальная модель и т.д.
AI_PROVIDER = os.environ.get('AI_PROVIDER', '').lower()  # пусто = заглушка
AI_API_KEY = os.environ.get('AI_API_KEY', '')


def is_enabled() -> bool:
    return bool(AI_PROVIDER and AI_API_KEY)


def summarize(text: str, *, max_chars: int = 3500) -> str:
    """
    Краткий пересказ конспекта. Сейчас — заглушка: возвращает сам текст,
    обрезанный до max_chars. Когда подключим ИИ — поменяется только тело
    этой функции, всё остальное в боте останется как есть.
    """
    if is_enabled():
        try:
            return _call_provider(text)
        except Exception as e:
            return f'⚠️ ИИ недоступен ({e}). Текст конспекта:\n\n{_truncate(text, max_chars)}'
    return _truncate(text, max_chars)


def _truncate(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + '\n…(обрезано)'


def _call_provider(text: str) -> str:
    """Реальный вызов ИИ. Заполнить, когда определимся с провайдером."""
    raise NotImplementedError('AI provider not implemented yet')
