import calendar as pycal
import datetime as dt
from vk_api.keyboard import VkKeyboard, VkKeyboardColor


def _nav(kb, with_back=True):
    kb.add_line()
    if with_back:
        kb.add_button('Назад', color=VkKeyboardColor.SECONDARY, payload={'state': 'back'})
    kb.add_button('Отмена', color=VkKeyboardColor.NEGATIVE, payload={'state': 'cancel'})

def kb_main():
    kb = VkKeyboard(one_time=False)
    kb.add_button('Предметы', color=VkKeyboardColor.PRIMARY, payload={'state': 'subjects'})
    kb.add_line()
    kb.add_button('Что скоро?', color=VkKeyboardColor.POSITIVE, payload={'state': 'upcoming'})
    kb.add_button('AI чат', color=VkKeyboardColor.SECONDARY, payload={'state': 'ai_chat'})
    return kb

def kb_ai_confirm():
    kb = VkKeyboard(one_time=False)
    kb.add_button('Подтвердить', color=VkKeyboardColor.POSITIVE, payload={'state': 'ai_confirm'})
    kb.add_line()
    kb.add_button('Не сохранять', color=VkKeyboardColor.NEGATIVE, payload={'state': 'ai_reject'})
    return kb

def kb_subjects(subjects: list[str]):
    kb = VkKeyboard(one_time=False)
    for i, s in enumerate(subjects):
        label = s if len(s) <= 40 else s[:37] + '...'
        kb.add_button(label, color=VkKeyboardColor.PRIMARY,
                      payload={'state': 'pick_subject', 'value': s})
        if (i + 1) % 2 == 0 and i != len(subjects) - 1:
            kb.add_line()
    if subjects:
        kb.add_line()
    kb.add_button('+ Добавить предмет', color=VkKeyboardColor.POSITIVE,
                  payload={'state': 'add_subject'})
    kb.add_line()
    kb.add_button('Назад', color=VkKeyboardColor.NEGATIVE, payload={'state': 'cancel'})
    return kb



def kb_subject_actions():
    kb = VkKeyboard(one_time=False)
    kb.add_button('Написать конспект', color=VkKeyboardColor.PRIMARY,
                  payload={'state': 'write_note'})
    kb.add_line()
    kb.add_button('Поиск / Просмотр', color=VkKeyboardColor.SECONDARY,
                  payload={'state': 'search'})
    kb.add_button('Загрузить конспект', color=VkKeyboardColor.SECONDARY,
                  payload={'state': 'load_note'})
    kb.add_line()
    kb.add_button('Назад к предметам', color=VkKeyboardColor.SECONDARY,
                  payload={'state': 'back_subjects'})
    kb.add_line()
    kb.add_button('Удалить предмет', color=VkKeyboardColor.NEGATIVE,
                  payload={'state': 'delete_subject'})
    return kb


def kb_search():
    kb = VkKeyboard(one_time=False)
    kb.add_button('По названию', color=VkKeyboardColor.PRIMARY,
                  payload={'state': 'search_title'})
    kb.add_button('По дате', color=VkKeyboardColor.PRIMARY,
                  payload={'state': 'search_date'})
    kb.add_line()
    kb.add_button('Все конспекты', color=VkKeyboardColor.SECONDARY,
                  payload={'state': 'search_all'})
    _nav(kb, with_back=False)
    return kb


def kb_cancel():
    kb = VkKeyboard(one_time=False)
    kb.add_button('Отмена', color=VkKeyboardColor.NEGATIVE, payload={'state': 'cancel'})
    return kb


def kb_yes_no_reminder():
    kb = VkKeyboard(one_time=False)
    kb.add_button('Да, поставить напоминание', color=VkKeyboardColor.POSITIVE,
                  payload={'state': 'rem_yes'})
    kb.add_line()
    kb.add_button('Нет', color=VkKeyboardColor.NEGATIVE, payload={'state': 'rem_no'})
    return kb


# ---- мини-календарь ----
def kb_years():
    kb = VkKeyboard(one_time=False)
    y = dt.date.today().year
    for yy in (y, y + 1, y + 2):
        kb.add_button(str(yy), color=VkKeyboardColor.PRIMARY,
                      payload={'state': 'year', 'value': yy})
    _nav(kb, with_back=False)  # назад с года = отмена
    return kb


def kb_months():
    kb = VkKeyboard(one_time=False)
    names = ['Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн',
             'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек']
    for i, n in enumerate(names, 1):
        kb.add_button(n, color=VkKeyboardColor.PRIMARY,
                      payload={'state': 'month', 'value': i})
        if i % 4 == 0 and i != 12:
            kb.add_line()
    _nav(kb)
    return kb


def kb_days(year: int, month: int):
    kb = VkKeyboard(one_time=False)
    days = pycal.monthrange(year, month)[1]
    for d in range(1, days + 1):
        kb.add_button(str(d), color=VkKeyboardColor.PRIMARY,
                      payload={'state': 'day', 'value': d})
        if d % 5 == 0 and d != days:
            kb.add_line()
    _nav(kb)
    return kb


def kb_hours():
    kb = VkKeyboard(one_time=False)
    for h in range(24):
        kb.add_button(f'{h:02d}', color=VkKeyboardColor.PRIMARY,
                      payload={'state': 'hour', 'value': h})
        if (h + 1) % 5 == 0 and h != 23:
            kb.add_line()
    _nav(kb)
    return kb


def kb_minutes():
    kb = VkKeyboard(one_time=False)
    for m in (0, 15, 30, 45):
        kb.add_button(f'{m:02d}', color=VkKeyboardColor.PRIMARY,
                      payload={'state': 'minute', 'value': m})
    _nav(kb)
    return kb
