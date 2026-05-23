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

Ты работаешь в чате учебного помощника.

Нужно:
1) ответить пользователю естественно;
2) если пользователь просит действие, вернуть structured action.

Поддерживаемые действия:
- save_summary
- create_reminder
- show_upcoming

Верни строго JSON:

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

Критически важные правила:
- reply — это единственный текст, который увидит пользователь.
- reply должен быть без markdown.
- не использовать технические слова: AI распознал, intent, JSON, действие, системное.
- если пользователь просит сохранить конспект, ОБЯЗАТЕЛЬНО:
  - intent = "save_summary"
  - content = полный текст, который нужно сохранить
  - если пользователь прислал просто кусок текста и просит "сохрани это", content должен быть этим текстом
  - если предмет не назван, но выбран текущий предмет, используй его
  - title сделай коротким и понятным
  - reply оформи так:

Если хотите, я могу сохранить это как конспект в таком виде:

Предмет: ...
Название: ...
Текст:
...

Сохранить?

- если пользователь просит создать напоминание, ОБЯЗАТЕЛЬНО:
  - intent = "create_reminder"
  - title = короткое название события
  - when_text = естественная русская дата/время, например "завтра в 12:00"
  - description = короткое описание
  - reply оформи в том же стиле, что и конспект:

Если хотите, я могу создать напоминание в таком виде:

Предмет: ...
Название: ...
Когда: ...
Описание: ...

Создать?

- если пользователь просит показать ближайшие события/напоминания/что скоро:
  - intent = "show_upcoming"
  - reply = короткий естественный текст, например "Сейчас покажу ближайшие события."
  - missing = []

- если данных для save_summary или create_reminder не хватает:
  - не предлагай подтверждение
  - задай уточняющий вопрос в reply
  - в action укажи missing и ask_user

Важно для save_summary:
- если пользователь пишет "сохрани как конспект: ..." или "сделай summary и сохрани", не оставляй content пустым
- content должен содержать текст для сохранения, а не краткое описание намерения
- нельзя возвращать save_summary с пустым content

Стиль персонажа:
{persona or "нейтральный вежливый стиль"}
""".strip()

    messages = [{'role': 'system', 'content': system}]
    messages += trim_history(history)
    messages.append({'role': 'user', 'content': user_text})

    resp = client.chat.completions.create(
        model=AI_MODEL,
        temperature=0.35,
        messages=messages,
    )

    raw = (resp.choices[0].message.content or '').strip()

    try:
        data = _extract_json(raw)
        reply = vk_plain_text(data.get('reply', '')).strip()
        action = _normalize_action(data.get('action'))

        if action and action.get('intent') == 'save_summary':
            content = (action.get('content') or '').strip()
            if not content:
                action['missing'] = list(set((action.get('missing') or []) + ['content']))
                if not action.get('ask_user'):
                    action['ask_user'] = 'Пришлите сам текст, который нужно сохранить как конспект.'

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