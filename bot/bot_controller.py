import re
import json
import datetime as dt

import dateparser
from vk_api.utils import get_random_id
from ai_helper import summarize, is_enabled as ai_on, chat_turn, trim_history

import vk_view as V

DATE_STEPS = ['year', 'month', 'day', 'hour', 'minute']


def _safe_filename(s: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', '', s).strip() or 'Без названия'


class BotController:
    def __init__(self, vk, db):
        self.vk = vk
        self.db = db

    def _services(self, user_id):
        from google_services import creds_from_json, CalendarAPI, DriveAPI
        creds = creds_from_json(self.db.get_creds(user_id))
        if creds is None:
            return None, None
        # обновлённый токен сохраняем
        self.db.set_creds(user_id, creds.to_json())
        return CalendarAPI(creds), DriveAPI(creds)

    # ---------- отправка ----------
    def send(self, peer_id, text, kb=None):
        p = {'peer_id': peer_id, 'message': text, 'random_id': get_random_id()}
        if kb is not None:
            p['keyboard'] = kb.get_keyboard()
        self.vk.method('messages.send', p)

    def send_main(self, peer_id, text='Главное меню:'):
        self.db.set_state(peer_id, 'main_menu')
        self.db.set_temp(peer_id, None)
        self.send(peer_id, text, V.kb_main())

    # ---------- вход ----------
    def handle(self, peer_id, text, payload):
        if self.db.get_creds(peer_id) is None:
            from auth_server import auth_url_for
            self.send(peer_id,
                      'Чтобы бот мог писать в твой Google Календарь и Drive, '
                      f'авторизуйся: {auth_url_for(peer_id)}')
            return

        state, subject, subjects, temp = self.db.get_user(peer_id)
        a = (payload or {}).get('a')
        v = (payload or {}).get('v')

        # глобальная отмена (но не во время выбора даты — там своя логика)
        if a == 'cancel' and not state.startswith('date_'):
            self.send_main(peer_id, 'Отменено.')
            return

        # роутинг по состоянию
        try:
            handler = getattr(self, f'st_{state}', None)
            if handler:
                handler(peer_id, text, a, v, subject, subjects, temp)
            else:
                self.send_main(peer_id)
        except Exception as e:
            self.send(peer_id, f'❌ Ошибка: {e}', V.kb_main())
            self.db.set_state(peer_id, 'main_menu')
            self.db.set_temp(peer_id, None)

    # ---------- состояния ----------
    def st_main_menu(self, pid, text, a, v, subj, subs, temp):
        if a == 'subjects':
            self.db.set_state(pid, 'selecting_subject')
            self.send(pid, 'Ваши предметы:' if subs else 'Предметов ещё нет.',
                      V.kb_subjects(subs))
        elif a == 'upcoming':
            self.show_upcoming(pid)
        elif a == 'ai_chat':
            self.db.set_state(pid, 'ai_chat')
            self.send(pid, 'AI-чат включён. Напишите сообщение. Для выхода нажмите «Отмена».', V.kb_cancel())
        else:
            self.send(pid, 'Пользуйся кнопками.', V.kb_main())

    def st_ai_chat(self, pid, text, a, v, subj, subs, temp):
        if a == 'cancel':
            self.send_main(pid, 'Выход из AI-чата.')
            return

        if not text:
            self.send(pid, 'Напишите сообщение.', V.kb_cancel())
            return

        if not ai_on():
            self.send(pid, 'AI сейчас не настроен.', V.kb_cancel())
            return

        history = self.db.get_ai_history(pid)
        persona = self.db.get_ai_persona(pid)

        turn = chat_turn(
            text,
            history,
            persona=persona,
            subjects=subs,
            current_subject=subj,
        )

        reply = (turn.get('reply') or '').strip() or 'Не удалось сформировать ответ.'
        action = turn.get('action')

        history = history + [
            {'role': 'user', 'content': text},
            {'role': 'assistant', 'content': reply},
        ]
        self.db.set_ai_history(pid, trim_history(history))

        if action and action.get('intent') in ('save_summary', 'create_reminder'):
            confidence = float(action.get('confidence', 0) or 0)
            missing = action.get('missing') or []

            if confidence >= 0.75 and not missing:
                self.db.set_pending_action(pid, action)
                self.db.set_state(pid, 'ai_confirm_action')
                self.send(pid, reply, V.kb_ai_confirm())
                return

        self.send(pid, reply, V.kb_cancel())

    def st_ai_confirm_action(self, pid, text, a, v, subj, subs, temp):
        if a == 'ai_reject':
            self.db.set_pending_action(pid, None)
            self.db.set_state(pid, 'ai_chat')
            self.send(pid, 'Хорошо, ничего не сохраняю.', V.kb_cancel())
            return

        if a != 'ai_confirm':
            self.send(pid, 'Нажмите «Подтвердить» или «Отмена».', V.kb_ai_confirm())
            return

        action = self.db.get_pending_action(pid)
        if not action:
            self.db.set_state(pid, 'ai_chat')
            self.send(pid, 'Действие не найдено.', V.kb_cancel())
            return

        if action['intent'] == 'save_summary':
            self._execute_ai_save_summary(pid, action, subj)
            return

        if action['intent'] == 'create_reminder':
            self._execute_ai_reminder(pid, action, subj)
            return

        self.db.set_pending_action(pid, None)
        self.db.set_state(pid, 'ai_chat')
        self.send(pid, 'Неизвестное действие.', V.kb_cancel())

    def _format_ai_action(self, action: dict) -> str:
        if action['intent'] == 'create_reminder':
            return (
                "AI распознал создание напоминания:\n\n"
                f"Название: {action.get('title') or 'Без названия'}\n"
                f"Когда: {action.get('when_text')}\n"
                f"Описание: {action.get('description') or '-'}\n\n"
                "Подтвердить?"
            )
        if action['intent'] == 'save_summary':
            preview = (action.get('content') or '')[:400]
            return (
                "AI распознал сохранение конспекта:\n\n"
                f"Предмет: {action.get('subject') or 'не указан'}\n"
                f"Название: {action.get('title') or 'Без названия'}\n"
                f"Текст:\n{preview}\n\n"
                "Подтвердить?"
            )
        return "Подтвердить действие?"

    def _execute_ai_reminder(self, pid, action, current_subject):
        import datetime as dt
        from dateparser import parse as dp_parse

        cal, drive = self._services(pid)

        when_text = action.get('when_text')
        start = dp_parse(
            when_text,
            languages=['ru'],
            settings={
                'TIMEZONE': 'Europe/Samara',
                'RETURN_AS_TIMEZONE_AWARE': False,
                'PREFER_DATES_FROM': 'future',
            }
        )
        if not start:
            self.db.set_state(pid, 'ai_chat')
            self.db.set_pending_action(pid, None)
            self.send(pid, 'Не смог понять дату/время. Напишите точнее, например: 25.05.2026 18:30', V.kb_cancel())
            return

        end = start + dt.timedelta(hours=1)

        subject = action.get('subject') or current_subject
        title = action.get('title') or 'Напоминание'
        if subject:
            summary = f'[{subject}] {title}'
        else:
            summary = title

        description = action.get('description') or ''

        ev = cal.add_event(summary, start, end, description=description)

        self.db.set_pending_action(pid, None)
        self.db.set_state(pid, 'ai_chat')
        self.send(pid, f'✅ Напоминание создано:\n{start:%d.%m.%Y %H:%M}\n{ev.get("htmlLink", "")}', V.kb_cancel())

    def _execute_ai_reminder(self, pid, action, current_subject):
        cal, drive = self._services(pid)

        when_text = (action.get('when_text') or '').strip()
        if not when_text:
            self.db.set_pending_action(pid, None)
            self.db.set_state(pid, 'ai_chat')
            self.send(pid, 'Не вижу дату и время. Напишите, например: завтра в 19:00.', V.kb_cancel())
            return

        start = dateparser.parse(
            when_text,
            languages=['ru'],
            settings={
                'TIMEZONE': 'Europe/Samara',
                'RETURN_AS_TIMEZONE_AWARE': False,
                'PREFER_DATES_FROM': 'future',
            }
        )

        if not start:
            self.db.set_pending_action(pid, None)
            self.db.set_state(pid, 'ai_chat')
            self.send(pid, 'Не смог понять дату и время. Напишите точнее, например: 25.05.2026 18:30.', V.kb_cancel())
            return

        end = start + dt.timedelta(hours=1)

        subject = (action.get('subject') or current_subject or '').strip()
        title = (action.get('title') or 'Напоминание').strip()
        description = (action.get('description') or '').strip()

        summary = f'[{subject}] {title}' if subject else title

        ev = cal.add_event(summary, start, end, description=description)

        self.db.set_pending_action(pid, None)
        self.db.set_state(pid, 'ai_chat')
        self.send(
            pid,
            f'✅ Напоминание создано на {start:%d.%m.%Y %H:%M}\n{ev.get("htmlLink", "")}',
            V.kb_cancel()
        )

    def st_selecting_subject(self, pid, text, a, v, subj, subs, temp):
        if a == 'add_subject':
            self.db.set_state(pid, 'wait_new_subject')
            self.send(pid, 'Введи название нового предмета:', V.kb_cancel())
        elif a == 'pick_subject' and v in subs:
            self.db.set_subject(pid, v)
            self.db.set_state(pid, 'subject_menu')
            self.send(pid, f'Выбран: {v}', V.kb_subject_actions())
        else:
            self.send(pid, 'Используй кнопки.', V.kb_subjects(subs))

    def st_wait_new_subject(self, pid, text, a, v, subj, subs, temp):
        if not text:
            self.send(pid, 'Нужен текст.', V.kb_cancel()); return
        ok = self.db.add_subject(pid, text.strip())
        msg = f"Добавлен '{text}'." if ok else 'Такой предмет уже есть.'
        _, _, subs, _ = self.db.get_user(pid)
        self.db.set_state(pid, 'selecting_subject')
        self.send(pid, msg, V.kb_subjects(subs))

    def st_subject_menu(self, pid, text, a, v, subj, subs, temp):
        if a == 'write_note':
            self.db.set_state(pid, 'wait_note_title')
            self.send(pid, 'Название конспекта:', V.kb_cancel())
        elif a == 'search':
            self.db.set_state(pid, 'search_menu')
            self.send(pid, 'Как ищем?', V.kb_search())
        elif a == 'back_subjects':
            self.db.set_state(pid, 'selecting_subject')
            self.send(pid, 'Предметы:', V.kb_subjects(subs))
        else:
            self.send(pid, 'Используй кнопки.', V.kb_subject_actions())

    def st_wait_note_title(self, pid, text, a, v, subj, subs, temp):
        if not text:
            self.send(pid, 'Нужен текст.', V.kb_cancel()); return
        self.db.set_temp(pid, {'title': text.strip()})
        self.db.set_state(pid, 'wait_note_text')
        self.send(pid, f"Название: «{text}».\nТеперь пришли текст конспекта:",
                  V.kb_cancel())

    def st_wait_note_text(self, pid, text, a, v, subj, subs, temp):
        cal, drive = self._services(pid)
        if not text:
            self.send(pid, 'Нужен текст.', V.kb_cancel()); return
        title = (temp or {}).get('title', 'Без названия')
        date_str = dt.datetime.now().strftime('%d.%m.%Y')
        filename = f"[{subj}] {_safe_filename(title)}.md"
        body = (f"Название: {title}\nПредмет: {subj}\nДата: {date_str}\n\n"
                f"Конспект:\n{text}")
        fid, link = drive.upload_note(filename, body)
        self.db.set_temp(pid, {'title': title, 'file_id': fid, 'link': link})
        self.db.set_state(pid, 'ask_reminder')
        self.send(pid,
                  f"✅ Сохранено на Drive:\n{link}\n\nПоставить напоминание в календаре?",
                  V.kb_yes_no_reminder())

    def st_ask_reminder(self, pid, text, a, v, subj, subs, temp):
        if a == 'rem_yes':
            self.db.set_state(pid, 'date_year')
            self.send(pid, 'Выбери год:', V.kb_years())
        elif a == 'rem_no':
            self.send_main(pid, 'Готово. Без напоминания.')
        else:
            self.send(pid, 'Выбери Да или Нет.', V.kb_yes_no_reminder())

    # ---- выбор даты ----
    def st_date_year(self, pid, text, a, v, subj, subs, temp):
        if a == 'cancel':
            self.send_main(pid, 'Отменено.'); return
        if a == 'year':
            self._date_set(pid, temp, 'year', v)
            self.db.set_state(pid, 'date_month')
            self.send(pid, f"Год: {v}\nМесяц:", V.kb_months())
        else:
            self.send(pid, 'Выбери год.', V.kb_years())

    def st_date_month(self, pid, text, a, v, subj, subs, temp):
        if a == 'cancel': self.send_main(pid, 'Отменено.'); return
        if a == 'back':
            self.db.set_state(pid, 'date_year'); self.send(pid, 'Год:', V.kb_years()); return
        if a == 'month':
            self._date_set(pid, temp, 'month', v)
            self.db.set_state(pid, 'date_day')
            self.send(pid, f"{temp['year']}-{v:02d}\nДень:",
                      V.kb_days(temp['year'], v))
        else:
            self.send(pid, 'Выбери месяц.', V.kb_months())

    def st_date_day(self, pid, text, a, v, subj, subs, temp):
        if a == 'cancel': self.send_main(pid, 'Отменено.'); return
        if a == 'back':
            self.db.set_state(pid, 'date_month'); self.send(pid, 'Месяц:', V.kb_months()); return
        if a == 'day':
            self._date_set(pid, temp, 'day', v)
            self.db.set_state(pid, 'date_hour')
            self.send(pid, 'Час:', V.kb_hours())
        else:
            self.send(pid, 'Выбери день.', V.kb_days(temp['year'], temp['month']))

    def st_date_hour(self, pid, text, a, v, subj, subs, temp):
        if a == 'cancel': self.send_main(pid, 'Отменено.'); return
        if a == 'back':
            self.db.set_state(pid, 'date_day')
            self.send(pid, 'День:', V.kb_days(temp['year'], temp['month'])); return
        if a == 'hour':
            self._date_set(pid, temp, 'hour', v)
            self.db.set_state(pid, 'date_minute')
            self.send(pid, 'Минуты:', V.kb_minutes())
        else:
            self.send(pid, 'Выбери час.', V.kb_hours())

    def st_date_minute(self, pid, text, a, v, subj, subs, temp):
        if a == 'cancel': self.send_main(pid, 'Отменено.'); return
        if a == 'back':
            self.db.set_state(pid, 'date_hour'); self.send(pid, 'Час:', V.kb_hours()); return
        if a == 'minute':
            self._date_set(pid, temp, 'minute', v)
            self._create_reminder(pid, subj, temp)
        else:
            self.send(pid, 'Выбери минуты.', V.kb_minutes())

    def _date_set(self, pid, temp, key, val):
        temp = dict(temp or {})
        temp[key] = val
        self.db.set_temp(pid, temp)

    def _create_reminder(self, pid, subject, temp):
        _, _, _, temp = self.db.get_user(pid)
        start = dt.datetime(temp['year'], temp['month'], temp['day'],
                            temp['hour'], temp['minute'])
        end = start + dt.timedelta(hours=1)
        title = temp.get('title', 'Конспект')
        link = temp.get('link', '')
        summary = f"[{subject}] {title}"
        desc = f"Конспект: {link}" if link else ''
        cal, drive = self._services(pid)
        ev = cal.add_event(summary, start, end, description=desc,
                                drive_file_id=temp.get('file_id'))
        self.send_main(pid,
                       f"✅ Напоминание создано на {start:%d.%m.%Y %H:%M}\n"
                       f"{ev.get('htmlLink', '')}")

    # ---- поиск ----
    def st_search_menu(self, pid, text, a, v, subj, subs, temp):
        if a == 'search_all':
            self._show_search(pid, subj, 'all', '')
        elif a == 'search_title':
            self.db.set_state(pid, 'wait_search_title')
            self.send(pid, 'Слово из названия:', V.kb_cancel())
        elif a == 'search_date':
            self.db.set_state(pid, 'wait_search_date')
            self.send(pid, 'Дата в формате ДД.ММ.ГГГГ:', V.kb_cancel())
        else:
            self.send(pid, 'Используй кнопки.', V.kb_search())

    def st_wait_search_title(self, pid, text, a, v, subj, subs, temp):
        if not text: self.send(pid, 'Нужен текст.', V.kb_cancel()); return
        self._show_search(pid, subj, 'title', text.strip())

    def st_wait_search_date(self, pid, text, a, v, subj, subs, temp):
        if not text: self.send(pid, 'Нужен текст.', V.kb_cancel()); return
        self._show_search(pid, subj, 'date', text.strip())

    def _show_search(self, pid, subject, kind, query):
        cal, drive = self._services(pid)
        notes = drive.list_notes(subject=subject)
        results = []
        for n in notes:
            name = n['name']
            if kind == 'title' and query.lower() not in name.lower():
                continue
            if kind == 'date':
                content = drive.download_text(n['id'])
                if f'Дата: {query}' not in content:
                    continue
            clean = name.replace(f'[{subject}] ', '').replace('.md', '')
            results.append(f"📄 {clean}\n{n['webViewLink']}")
        if not results:
            txt = 'Ничего не найдено.'
        else:
            txt = f"Найдено ({len(results)}):\n\n" + '\n\n'.join(results[:10])
            if len(results) > 10:
                txt += f"\n\n…ещё {len(results) - 10}"
        self.db.set_state(pid, 'subject_menu')
        self.send(pid, txt, V.kb_subject_actions())

    # ---- что скоро ----
    def show_upcoming(self, pid):
        cal, drive = self._services(pid)
        events = cal.list_upcoming(days=7)
        events = [e for e in events if cal.get_drive_id(e)]
        if not events:
            self.send(pid, 'Ближайших напоминаний нет.', V.kb_main()); return
        for ev in events[:5]:
            start = ev['start'].get('dateTime', ev['start'].get('date'))
            fid = self.cal_helper.get_drive_id(ev) if False else None  # см. ниже
            fid = ev.get('extendedProperties', {}).get('private', {}).get('driveFileId')
            try:
                raw = drive.download_text(fid)
                body = summarize(raw)
            except Exception as e:
                body = f'(не удалось получить файл: {e})'
            prefix = '🤖 Краткий пересказ:' if ai_on() else '📝 Текст конспекта:'
            self.send(pid,
                      f"⏰ {ev.get('summary', '')}\n🕒 {start}\n\n{prefix}\n{body}")
        self.send(pid, 'Главное меню:', V.kb_main())
        self.db.set_state(pid, 'main_menu')
