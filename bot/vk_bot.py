from dotenv import load_dotenv
load_dotenv()
import os
import json
import calendar as pycal
import datetime as dt
from typing import Dict, Any

import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from vk_api.utils import get_random_id

from calendar_api import add_event

VK_TOKEN = os.environ["VK_GROUP_TOKEN"]
VK_GROUP_ID = int(os.environ["VK_GROUP_ID"])

STUB_TITLE = "Запись из VK бота"
EVENT_DURATION = dt.timedelta(hours=1)

# ---------- состояние пользователей ----------
# step: idle | year | month | day | hour | minute | text
STEPS_ORDER = ["year", "month", "day", "hour", "minute", "text"]
states: Dict[int, Dict[str, Any]] = {}

def get_state(uid: int) -> Dict[str, Any]:
    return states.setdefault(uid, {"step": "idle", "data": {}})

def reset(uid: int) -> None:
    states[uid] = {"step": "idle", "data": {}}


# ---------- клавиатуры ----------
def _add_nav(kb: VkKeyboard, with_back: bool = True) -> None:
    kb.add_line()
    if with_back:
        kb.add_button("Назад", color=VkKeyboardColor.SECONDARY, payload={"a": "back"})
    kb.add_button("Отмена", color=VkKeyboardColor.NEGATIVE, payload={"a": "cancel"})

def kb_main() -> VkKeyboard:
    kb = VkKeyboard(one_time=False)
    kb.add_button("Начать", color=VkKeyboardColor.POSITIVE, payload={"a": "start"})
    kb.add_button("Остановить", color=VkKeyboardColor.NEGATIVE, payload={"a": "stop"})
    return kb

def kb_years() -> VkKeyboard:
    kb = VkKeyboard(one_time=False)
    y0 = dt.date.today().year
    for y in (y0, y0 + 1, y0 + 2):
        kb.add_button(str(y), color=VkKeyboardColor.PRIMARY, payload={"a": "year", "v": y})
    # назад с года = отмена, поэтому без "Назад"
    _add_nav(kb, with_back=False)
    return kb

def kb_months() -> VkKeyboard:
    kb = VkKeyboard(one_time=False)
    names = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн",
             "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]
    for i, name in enumerate(names, 1):
        kb.add_button(name, color=VkKeyboardColor.PRIMARY,
                      payload={"a": "month", "v": i})
        if i % 4 == 0 and i != 12:
            kb.add_line()
    _add_nav(kb)
    return kb

def kb_days(year: int, month: int) -> VkKeyboard:
    kb = VkKeyboard(one_time=False)
    days = pycal.monthrange(year, month)[1]
    for d in range(1, days + 1):
        kb.add_button(str(d), color=VkKeyboardColor.PRIMARY,
                      payload={"a": "day", "v": d})
        if d % 5 == 0 and d != days:
            kb.add_line()
    _add_nav(kb)
    return kb

def kb_hours() -> VkKeyboard:
    kb = VkKeyboard(one_time=False)
    for h in range(24):
        kb.add_button(f"{h:02d}", color=VkKeyboardColor.PRIMARY,
                      payload={"a": "hour", "v": h})
        if (h + 1) % 5 == 0 and h != 23:
            kb.add_line()
    _add_nav(kb)
    return kb

def kb_minutes() -> VkKeyboard:
    kb = VkKeyboard(one_time=False)
    for m in (0, 15, 30, 45):
        kb.add_button(f"{m:02d}", color=VkKeyboardColor.PRIMARY,
                      payload={"a": "minute", "v": m})
    _add_nav(kb)
    return kb

def kb_text_step() -> VkKeyboard:
    kb = VkKeyboard(one_time=False)
    kb.add_button("Пропустить", color=VkKeyboardColor.PRIMARY,
                  payload={"a": "skip_text"})
    _add_nav(kb)
    return kb


# ---------- хелперы ----------
def send(vk, peer_id: int, text: str, keyboard: VkKeyboard | None = None) -> None:
    params = {"peer_id": peer_id, "message": text, "random_id": get_random_id()}
    if keyboard is not None:
        params["keyboard"] = keyboard.get_keyboard()
    vk.method("messages.send", params)


def prompt_step(vk, peer_id: int, state: Dict[str, Any]) -> None:
    step = state["step"]
    d = state["data"]
    if step == "year":
        send(vk, peer_id, "Выбери год:", kb_years())
    elif step == "month":
        send(vk, peer_id, f"Год: {d['year']}\nВыбери месяц:", kb_months())
    elif step == "day":
        send(vk, peer_id, f"{d['year']}-{d['month']:02d}\nВыбери день:",
             kb_days(d["year"], d["month"]))
    elif step == "hour":
        send(vk, peer_id,
             f"{d['year']}-{d['month']:02d}-{d['day']:02d}\nВыбери час:", kb_hours())
    elif step == "minute":
        send(vk, peer_id,
             f"{d['year']}-{d['month']:02d}-{d['day']:02d} {d['hour']:02d}:--\n"
             f"Выбери минуты:", kb_minutes())
    elif step == "text":
        send(vk, peer_id,
             f"Дата: {d['year']}-{d['month']:02d}-{d['day']:02d} "
             f"{d['hour']:02d}:{d['minute']:02d}\n"
             f"Введи текст события (или нажми «Пропустить»):",
             kb_text_step())


def go_back(state: Dict[str, Any]) -> str | None:
    """Возвращает имя нового шага или None, если это была отмена."""
    step = state["step"]
    idx = STEPS_ORDER.index(step)
    if idx == 0:
        return None
    prev = STEPS_ORDER[idx - 1]
    # удалим значение, выбранное на предыдущем шаге, чтобы переспросить
    state["data"].pop(prev, None)
    state["step"] = prev
    return prev


def finalize(vk, peer_id: int, state: Dict[str, Any], user_text: str) -> None:
    d = state["data"]
    start = dt.datetime(d["year"], d["month"], d["day"], d["hour"], d["minute"])
    end = start + EVENT_DURATION
    try:
        res = add_event(STUB_TITLE, start, end,
                        description=user_text or STUB_TITLE)
        send(vk, peer_id,
             f"✅ Создано: {start:%Y-%m-%d %H:%M}\n{res.get('htmlLink', '')}",
             kb_main())
    except Exception as e:
        send(vk, peer_id, f"❌ Ошибка при создании события:\n{e}", kb_main())
    reset(peer_id)


# ---------- основной цикл ----------
def main() -> None:
    session = vk_api.VkApi(token=VK_TOKEN)
    longpoll = VkBotLongPoll(session, VK_GROUP_ID)
    print("Bot started")

    for event in longpoll.listen():
        if event.type != VkBotEventType.MESSAGE_NEW:
            continue

        msg = event.obj.message
        peer_id = msg["peer_id"]
        text = (msg.get("text") or "").strip()
        payload = None
        if msg.get("payload"):
            try:
                payload = json.loads(msg["payload"])
            except json.JSONDecodeError:
                payload = None

        state = get_state(peer_id)
        a = (payload or {}).get("a")
        v = (payload or {}).get("v")

        # ---- глобальные команды ----
        if a == "cancel":
            reset(peer_id)
            send(session, peer_id, "Отменено.", kb_main())
            continue

        if a == "stop":
            reset(peer_id)
            send(session, peer_id, "Остановлено.", kb_main())
            continue

        if a == "start":
            reset(peer_id)
            state = get_state(peer_id)
            state["step"] = "year"
            prompt_step(session, peer_id, state)
            continue

        if a == "back":
            new_step = go_back(state)
            if new_step is None:
                reset(peer_id)
                send(session, peer_id, "Отменено.", kb_main())
            else:
                prompt_step(session, peer_id, state)
            continue

        # ---- шаги ----
        step = state["step"]

        if step == "idle":
            send(session, peer_id,
                 "Привет! Жми «Начать», чтобы создать запись в календаре.",
                 kb_main())
            continue

        # обработка выбора по payload
        expected = {
            "year": "year", "month": "month", "day": "day",
            "hour": "hour", "minute": "minute",
        }
        if step in expected and a == expected[step]:
            state["data"][step] = v
            next_idx = STEPS_ORDER.index(step) + 1
            state["step"] = STEPS_ORDER[next_idx]
            prompt_step(session, peer_id, state)
            continue

        # шаг ввода текста
        if step == "text":
            if a == "skip_text":
                finalize(session, peer_id, state, STUB_TITLE)
                continue
            if text:
                finalize(session, peer_id, state, text)
                continue

        # всё остальное
        send(session, peer_id, "Используй кнопки ниже 🙂", kb_main())


if __name__ == "__main__":
    main()
