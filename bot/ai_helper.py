import os
import json
from openai import OpenAI

AI_API_KEY = os.environ.get('AI_API_KEY', '')
AI_BASE_URL = os.environ.get('AI_BASE_URL', 'https://openai.bothub.chat/v1')
AI_MODEL = os.environ.get('AI_MODEL', 'gpt-4.5-mini')

client = OpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL) if AI_API_KEY else None

BASE_IDENTITY = (
    "Ты — Эдельвейз. Интеллектуальный помощник для обучения. "
    "Отвечай вежливо, ясно и полезно. "
    "Если задан стиль персонажа, используй только стиль речи, "
    "но не выдумывай лишние факты о себе."
)

def is_enabled() -> bool:
    return client is not None

def trim_history(messages: list[dict], max_messages: int = 10, max_chars: int = 4000) -> list[dict]:
    out = []
    total = 0
    for msg in reversed(messages[-max_messages:]):
        content = msg.get('content', '') or ''
        ln = len(content)
        if out and total + ln > max_chars:
            break
        out.append(msg)
        total += ln
    return list(reversed(out))

def chat_reply(user_text: str, history: list[dict], persona: str = '') -> str:
    if not is_enabled():
        return '⚠️ AI не настроен.'

    system = BASE_IDENTITY
    if persona:
        system += f"\n\nСтиль персонажа:\n{persona}"

    messages = [{'role': 'system', 'content': system}]
    messages += trim_history(history)
    messages.append({'role': 'user', 'content': user_text})

    resp = client.chat.completions.create(
        model=AI_MODEL,
        temperature=0.7,
        messages=messages,
    )
    return (resp.choices[0].message.content or '').strip()

def detect_action(history: list[dict], subjects: list[str], current_subject: str | None) -> dict:
    if not is_enabled():
        return {'intent': 'none', 'confidence': 0.0}

    prompt = f"""
        Ты анализируешь диалог и должен вернуть только JSON.
        
        Определи, есть ли намерение:
        1) создать напоминание в календаре
        2) сохранить конспект/summary по предмету
        
        Текущий предмет: {current_subject}
        Список предметов: {subjects}
        
        Верни JSON строго такого вида:
        {{
          "intent": "none|create_reminder|save_summary",
          "confidence": 0.0,
          "subject": null,
          "title": null,
          "content": null,
          "when_text": null,
          "description": null,
          "missing": [],
          "ask_user": null
        }}
        
        Правила:
        - intent = "none", если явного запроса нет.
        - create_reminder: когда пользователь просит напомнить, записать, поставить напоминание, событие.
        - save_summary: когда пользователь просит сохранить конспект, заметку, summary, запись по предмету.
        - subject бери из текущего предмета, если он очевиден и в сообщениях не указан другой.
        - when_text оставляй в естественном русском виде, например "завтра в 18:00".
        - если данных не хватает, укажи missing и ask_user.
        - Верни только JSON, без пояснений.
    """

    messages = [
        {'role': 'system', 'content': prompt},
        *trim_history(history)
    ]

    resp = client.chat.completions.create(
        model=AI_MODEL,
        temperature=0,
        messages=messages,
    )
    raw = (resp.choices[0].message.content or '').strip()
    return json.loads(raw)

def summarize(text: str, *, max_chars: int = 3500) -> str:
    if not is_enabled():
        return text if len(text) <= max_chars else text[:max_chars] + '\n…(обрезано)'

    prompt = (
        "Сделай краткий полезный пересказ учебного конспекта. "
        "Выдели основные идеи, определения и важные пункты."
    )

    resp = client.chat.completions.create(
        model=AI_MODEL,
        temperature=0.3,
        messages=[
            {'role': 'system', 'content': prompt},
            {'role': 'user', 'content': text[:12000]}
        ],
    )
    return (resp.choices[0].message.content or '').strip()
