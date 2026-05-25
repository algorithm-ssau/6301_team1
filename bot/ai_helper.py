import os
import json
import re

from openai import OpenAI

AI_API_KEY = os.environ.get('AI_API_KEY', '')
AI_BASE_URL = os.environ.get('AI_BASE_URL', 'https://openai.bothub.chat/v1')
AI_MODEL = os.environ.get('AI_MODEL', 'gpt-5.4-mini')

client = OpenAI(
    api_key=AI_API_KEY,
    base_url=AI_BASE_URL,
) if AI_API_KEY else None

BASE_IDENTITY = (
    "Ты — Эдельвейз, интеллектуальный помощник для обучения. "
    "Отвечай вежливо, ясно и полезно. "
    "Если задан стиль персонажа, он влияет только на манеру речи, "
    "но не меняет факты, логику и правила работы."
)

VK_STYLE_RULES = (
    "Пиши обычным текстом для VK-мессенджера. "
    "Запрещены любые элементы markdown: #, ##, **, __, *, `, ```. "
    "Для списков используй символ • или —. "
    "Не упоминай технические детали: intent, JSON, action, confidence, AI распознал."
)


def is_enabled() -> bool:
    return client is not None


def _truncate(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + '\n…(обрезано)'


def trim_history(messages: list[dict], max_messages: int = 10, max_chars: int = 4000) -> list[dict]:
    out = []
    total = 0
    for msg in reversed(messages[-max_messages:]):
        content = msg.get('content', '') or ''
        ln = len(content)
        if out and total + ln > max_chars:
            break
        out.append({
            'role': msg.get('role', 'user'),
            'content': content
        })
        total += ln
    return list(reversed(out))


def vk_plain_text(text: str) -> str:
    if not text:
        return ''
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*```$', '', text)
    text = re.sub(r'^\s{0,3}#{1,6}\s*', '', text, flags=re.MULTILINE)
    text = text.replace('**', '')
    text = text.replace('__', '')
    text = text.replace('`', '')
    text = re.sub(r'^\s*[-*]\s+', '• ', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _extract_json(raw: str) -> dict:
    """Извлекает JSON из ответа модели с несколькими стратегиями."""
    raw = (raw or '').strip()

    # Стратегия 1: убрать code fences
    cleaned = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*```$', '', cleaned).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # Стратегия 2: найти первый полный JSON-объект верхнего уровня
    # (учитываем вложенные скобки)
    start = raw.find('{')
    if start != -1:
        depth = 0
        in_string = False
        escape_next = False
        for i in range(start, len(raw)):
            ch = raw[i]
            if escape_next:
                escape_next = False
                continue
            if ch == '\\' and in_string:
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[start:i + 1])
                    except Exception:
                        break
        # Fallback: от первой до последней скобки
        end = raw.rfind('}')
        if end > start:
            try:
                return json.loads(raw[start:end + 1])
            except Exception:
                pass

    raise ValueError('JSON not found in AI response')


def _normalize_action(action: dict | None) -> dict | None:
    if not action or not isinstance(action, dict):
        return None

    intent = (action.get('intent') or 'none').strip().lower()

    valid_intents = ('save_summary', 'create_reminder', 'show_upcoming')
    if intent not in valid_intents:
        return None

    confidence = 0.0
    try:
        confidence = float(action.get('confidence', 0) or 0)
    except (TypeError, ValueError):
        confidence = 0.0

    # Гарантируем, что confidence в диапазоне [0, 1]
    confidence = max(0.0, min(1.0, confidence))

    out = {
        'intent': intent,
        'confidence': confidence,
        'subject': (action.get('subject') or None),
        'title': (action.get('title') or None),
        'content': (action.get('content') or None),
        'when_text': (action.get('when_text') or None),
        'description': (action.get('description') or None),
        'missing': list(action.get('missing') or []),
        'ask_user': (action.get('ask_user') or None),
    }

    return out


def _validate_action(action: dict | None) -> dict | None:
    if not action:
        return None

    intent = action['intent']

    if intent == 'save_summary':
        content = (action.get('content') or '').strip()
        if not content or len(content) < 10:
            if 'content' not in action['missing']:
                action['missing'].append('content')
            if not action.get('ask_user'):
                action['ask_user'] = 'Пришлите текст, который нужно сохранить как конспект.'
            action['confidence'] = min(action.get('confidence', 0), 0.4)

    elif intent == 'create_reminder':
        when_text = (action.get('when_text') or '').strip()
        title = (action.get('title') or '').strip()
        if not when_text:
            if 'when_text' not in action['missing']:
                action['missing'].append('when_text')
            if not action.get('ask_user'):
                action['ask_user'] = 'Укажите дату и время для напоминания.'
            action['confidence'] = min(action.get('confidence', 0), 0.4)
        if not title:
            if 'title' not in action['missing']:
                action['missing'].append('title')

    return action

def chat_turn(
    user_text: str,
    history: list[dict],
    *,
    persona: str = '',
    subjects: list[str] | None = None,
    current_subject: str | None = None,
) -> dict:
    if not is_enabled():
        return {
            'reply': '⚠️ AI не настроен.',
            'action': None,
        }

    subjects = subjects or []

    system = _build_system_prompt(
        persona=persona,
        subjects=subjects,
        current_subject=current_subject,
    )

    messages = [{'role': 'system', 'content': system}]
    messages += trim_history(history)
    messages.append({'role': 'user', 'content': user_text})

    try:
        resp = client.chat.completions.create(
            model=AI_MODEL,
            temperature=0.35,
            messages=messages,
        )
        raw = (resp.choices[0].message.content or '').strip()
    except Exception as e:
        return {
            'reply': f'Ошибка при обращении к AI: {e}',
            'action': None,
        }

    # Пытаемся распарсить как JSON
    try:
        data = _extract_json(raw)
        reply = vk_plain_text(data.get('reply', '')).strip()
        action = _normalize_action(data.get('action'))
        action = _validate_action(action)

        if not reply:
            reply = 'Не удалось сформировать ответ.'

        return {
            'reply': reply,
            'action': action,
        }
    except Exception:
        pass

    # Fallback: модель вернула просто текст (не JSON)
    # Делаем второй короткий запрос только для определения action
    plain_reply = vk_plain_text(raw) or 'Не удалось сформировать ответ.'

    action = _try_detect_action(user_text, plain_reply, current_subject)
    action = _validate_action(action)

    return {
        'reply': plain_reply,
        'action': action,
    }


def _try_detect_action(user_text: str, ai_reply: str, current_subject: str | None) -> dict | None:
    """Отдельный короткий запрос для определения action, если основной не вернул JSON."""
    if not is_enabled():
        return None

    detect_prompt = f"""Пользователь написал: "{user_text}"
Помощник ответил: "{ai_reply}"
Текущий предмет: {current_subject or "не выбран"}

Определи, нужно ли выполнить действие. Верни ТОЛЬКО JSON (без текста до/после):

Если действие не нужно:
{{"action": null}}

Если нужно:
{{"action": {{"intent": "save_summary|create_reminder|show_upcoming", "confidence": 0.0, "subject": null, "title": null, "content": null, "when_text": null, "description": null, "missing": [], "ask_user": null}}}}

Правила:
- save_summary: content обязателен (полный текст для сохранения)
- create_reminder: when_text и title обязательны
- show_upcoming: не требует параметров
- Если данных не хватает, укажи missing и ask_user
"""

    try:
        resp = client.chat.completions.create(
            model=AI_MODEL,
            temperature=0.1,
            messages=[{'role': 'user', 'content': detect_prompt}],
        )
        raw = (resp.choices[0].message.content or '').strip()
        data = _extract_json(raw)
        return _normalize_action(data.get('action'))
    except Exception:
        return None


def _build_system_prompt(
    persona: str = '',
    subjects: list[str] | None = None,
    current_subject: str | None = None,
) -> str:
    subjects = subjects or []

    return f"""{BASE_IDENTITY}

{VK_STYLE_RULES}

Контекст пользователя:
- Текущий предмет: {current_subject or "не выбран"}
- Предметы: {", ".join(subjects) if subjects else "нет"}

Ты умеешь выполнять три действия:
1) save_summary — сохранить конспект на Google Drive (пользователь может прислать текст или прикрепить файл, его содержимое придёт в тексте сообщения с меткой [Файл: ...])
2) create_reminder — создать напоминание в Google Calendar
3) show_upcoming — показать ближайшие события


Ответ — строго JSON без текста до или после:

{{
  "reply": "текст ответа пользователю (без markdown)",
  "action": null или объект действия
}}

Объект действия:
{{
  "intent": "save_summary | create_reminder | show_upcoming",
  "confidence": число от 0 до 1,
  "subject": "предмет или null",
  "title": "название или null",
  "content": "полный текст конспекта или null",
  "when_text": "дата/время на русском или null",
  "description": "описание или null",
  "missing": ["список недостающих полей"],
  "ask_user": "вопрос пользователю или null"
}}

Правила для reply:
- Это единственное, что видит пользователь
- Без markdown, без технических терминов

Правила для save_summary:
- content ОБЯЗАТЕЛЕН — это ДОСЛОВНЫЙ полный текст, который пользователь хочет сохранить
- НИКОГДА не сокращай, не пересказывай и не заменяй content на описание или заголовок
- Если пользователь написал текст и попросил сохранить — content = весь этот текст ЦЕЛИКОМ, как есть
- Если пользователь назвал тему но НЕ прислал сам текст конспекта — content оставь null, добавь "content" в missing, попроси прислать текст
- Если предмет не указан, но есть текущий — используй текущий
- title сделай коротким и понятным (до 40 символов)
- confidence ставь 0.9 ТОЛЬКО когда content содержит реальный текст длиной больше 10 символов
- В reply покажи:
  Предмет: ...
  Название: ...
  Текст: (первые 100 символов content)...
  Сохранить?


Правила для create_reminder:
- when_text и title обязательны
- Если не хватает данных — спроси в reply, добавь в missing
- В reply предложи подтверждение:
  Название: ...
  Когда: ...
  Создать?

Правила для show_upcoming:
- Не требует параметров, confidence = 0.9

Если действие не нужно — action = null.
Если данных не хватает — задай вопрос в reply, укажи missing, confidence < 0.5.

Стиль: {persona or "нейтральный вежливый"}""".strip()


def summarize(text: str, *, max_chars: int = 3500) -> str:
    if not is_enabled():
        return vk_plain_text(_truncate(text, max_chars))

    prompt = (
        "Сделай краткий полезный пересказ учебного конспекта. "
        "Пиши обычным текстом для VK. "
        "Без markdown, без #, без **, без code block. "
        "Если нужен список, используй •."
    )

    try:
        resp = client.chat.completions.create(
            model=AI_MODEL,
            temperature=0.3,
            messages=[
                {'role': 'system', 'content': prompt},
                {'role': 'user', 'content': text[:12000]},
            ],
        )
        return vk_plain_text((resp.choices[0].message.content or '').strip())
    except Exception:
        return vk_plain_text(_truncate(text, max_chars))
