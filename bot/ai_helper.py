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
    "Ты — Эдельвейз. Интеллектуальный помощник для обучения. "
    "Отвечай вежливо, ясно и полезно. "
    "Если задан стиль персонажа, он влияет только на манеру речи, "
    "но не меняет факты, логику и правила работы."
)

VK_STYLE_RULES = (
    "Пиши обычным текстом для VK. "
    "Не используй markdown и html. "
    "Запрещены: #, ##, **, __, *, `, ``` . "
    "Не делай markdown-списки. "
    "Если нужен список, используй строки с символом • или —. "
    "Не используй технические формулировки вроде "
    "'AI распознал', 'intent', 'JSON', 'системное действие', "
    "'распознано намерение'."
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

    # убрать code fences
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*```$', '', text)

    # заголовки markdown
    text = re.sub(r'^\s{0,3}#{1,6}\s*', '', text, flags=re.MULTILINE)

    # жирный / курсив / inline code
    text = text.replace('**', '')
    text = text.replace('__', '')
    text = text.replace('`', '')

    # markdown bullets -> обычные bullets
    text = re.sub(r'^\s*[-*]\s+', '• ', text, flags=re.MULTILINE)

    # лишние пустые строки
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def _extract_json(raw: str) -> dict:
    raw = (raw or '').strip()

    if raw.startswith('```'):
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.IGNORECASE)
        raw = re.sub(r'\s*```$', '', raw)

    try:
        return json.loads(raw)
    except Exception:
        pass

    start = raw.find('{')
    end = raw.rfind('}')
    if start != -1 and end != -1 and end > start:
        return json.loads(raw[start:end + 1])

    raise ValueError('JSON not found')


def _normalize_action(action: dict | None) -> dict | None:
    if not action or not isinstance(action, dict):
        return None

    intent = (action.get('intent') or 'none').strip()

    if intent not in ('save_summary', 'create_reminder', 'show_upcoming'):
        return None

    out = {
        'intent': intent,
        'confidence': float(action.get('confidence', 0) or 0),
        'subject': action.get('subject'),
        'title': action.get('title'),
        'content': action.get('content'),
        'when_text': action.get('when_text'),
        'description': action.get('description'),
        'missing': action.get('missing') or [],
        'ask_user': action.get('ask_user'),
    }

    return out

def chat_turn(
    user_text: str,
    history: list[dict],
    *,
    persona: str = '',
    subjects: list[str] | None = None,
    current_subject: str | None = None,
) -> dict:
    """
    Один вызов модели:
    - формирует естественный ответ пользователю
    - при необходимости прикладывает structured action
    """
    if not is_enabled():
        return {
            'reply': '⚠️ AI не настроен.',
            'action': None,
        }

    subjects = subjects or []

    system = f"""
{BASE_IDENTITY}

{VK_STYLE_RULES}

Контекст пользователя:
- Текущий выбранный предмет: {current_subject or "не выбран"}
- Список предметов: {", ".join(subjects) if subjects else "пусто"}

Твоя задача:
1) Ответить пользователю естественно.
2) Понять, хочет ли он:
   - сохранить конспект / summary / заметку
   - создать напоминание / событие / запись в календарь
   - показать ближайшие события / напоминания / расписание

Верни строго JSON такого вида:
{{
  "reply": "готовый текст ответа пользователю",
  "action": null
}}

или

{{
  "reply": "готовый текст ответа пользователю",
  "action": {{
    "intent": "save_summary|create_reminder|show_upcoming",
    "confidence": 0.0,
    "subject": null,
    "title": null,
    "content": null,
    "when_text": null,
    "description": null,
    "missing": [],
    "ask_user": null
  }}
}}

Правила:
- reply — это единственный текст, который увидит пользователь.
- reply должен быть естественным, без технических пояснений.
- Не пиши фразы вроде: "AI распознал", "намерение", "intent", "JSON", "системное действие".
- Если пользователь явно хочет сохранить конспект, reply должен уже мягко включать предложение сохранения.
- Если пользователь явно хочет создать напоминание, reply должен уже мягко включать предложение создать его.
- Если пользователь просит показать, что скоро, какие ближайшие события, что в календаре, какие напоминания впереди, верни action.intent = "show_upcoming".
- Для show_upcoming:
  - missing должен быть пустым списком
  - subject/title/content/when_text/description можно оставить null
  - reply можно сделать коротким, например: "Сейчас покажу ближайшие события."
- Если данных не хватает, не предлагай подтверждение. Вместо этого естественно уточни недостающие детали.
  В action тогда укажи missing и ask_user.
- Если действия нет, верни action = null.
- Для save_summary:
  - subject бери из сообщения, а если его нет — из текущего предмета, если это уместно.
  - title сделай коротким и понятным.
  - content должен содержать именно тот текст, который предлагается сохранить.
- Для create_reminder:
  - when_text оставляй как естественную русскую фразу, например "завтра в 18:00".
  - title — короткий заголовок.
- reply должен быть без markdown.

Стиль персонажа:
{persona or "нейтральный вежливый стиль"}
""".strip()

    messages = [{'role': 'system', 'content': system}]
    messages += trim_history(history)
    messages.append({'role': 'user', 'content': user_text})

    resp = client.chat.completions.create(
        model=AI_MODEL,
        temperature=0.5,
        messages=messages,
    )

    raw = (resp.choices[0].message.content or '').strip()

    try:
        data = _extract_json(raw)
        reply = vk_plain_text(data.get('reply', '')).strip()
        action = _normalize_action(data.get('action'))

        if not reply:
            reply = 'Не удалось сформировать ответ.'

        return {
            'reply': reply,
            'action': action,
        }
    except Exception:
        return {
            'reply': vk_plain_text(raw) or 'Не удалось сформировать ответ.',
            'action': None,
        }



def summarize(text: str, *, max_chars: int = 3500) -> str:
    if not is_enabled():
        return vk_plain_text(_truncate(text, max_chars))

    prompt = (
        "Сделай краткий полезный пересказ учебного конспекта. "
        "Пиши обычным текстом для VK. "
        "Без markdown, без #, без **, без code block. "
        "Если нужен список, используй •."
    )

    resp = client.chat.completions.create(
        model=AI_MODEL,
        temperature=0.3,
        messages=[
            {'role': 'system', 'content': prompt},
            {'role': 'user', 'content': text[:12000]},
        ],
    )

    return vk_plain_text((resp.choices[0].message.content or '').strip())