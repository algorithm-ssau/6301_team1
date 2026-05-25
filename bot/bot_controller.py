import re
import json
import datetime as dt

import dateparser
import requests
from vk_api.utils import get_random_id

from db import Database
from ai_helper import summarize, is_enabled as ai_on, chat_turn, trim_history
import vk_view as V


def _safe_filename(s: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', '', s).strip() or 'Без названия'

_MAX_FILE_BYTES = 2 * 1024 * 1024
_TEXT_EXTENSIONS = {'txt', 'md', 'text', 'csv', 'log', 'json', 'xml', 'html', 'htm', 'py', 'js', 'css'}


class BotController:
    def __init__(self, vk, db):
        self.vk = vk
        self.db: Database = db

    def _services(self, user_id):
        from google_services import creds_from_json, CalendarAPI, DriveAPI
        creds = creds_from_json(self.db.get_creds(user_id))
        if creds is None:
            return None, None
        self.db.set_creds(user_id, creds.to_json())
        return CalendarAPI(creds), DriveAPI(creds)

    def _require_services(self, user_id):
        cal, drive = self._services(user_id)
        if cal is not None and drive is not None:
            return cal, drive
        from auth_server import auth_url_for
        self.send(user_id, f'Нужна авторизация Google: {auth_url_for(user_id)}')
        return None, None

    def _payload_state(self, payload: dict | None) -> str | None:
        return (payload or {}).get('state')

    def _payload_value(self, payload: dict | None):
        payload = payload or {}
        if 'value' in payload:
            return payload.get('value')
        return payload.get('v')

    def _get_temp(self, pid: int) -> dict:
        raw = self.db.get_temp(pid)
        if not raw:
            return {}
        data = raw
        for _ in range(2):
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception:
                    return {}
        return data if isinstance(data, dict) else {}

    def _set_temp(self, pid: int, data: dict | None) -> None:
        self.db.set_temp(pid, data)

    def _download_doc(self, doc: dict) -> tuple[str, str] | None:
        ext = (doc.get('ext') or '').lower()
        if ext not in _TEXT_EXTENSIONS:
            return None
        size = doc.get('size', 0)
        if size > _MAX_FILE_BYTES:
            return None
        url = doc.get('url')
        if not url:
            return None
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            body = r.content.decode('utf-8', errors='replace')
        except Exception:
            return None
        title = (doc.get('title') or 'Без названия').strip()
        if title.lower().endswith(f'.{ext}'):
            title = title[:-(len(ext) + 1)].strip()
        return title, body

    def _save_note_to_drive(self, pid: int, subject: str, title: str, content: str) -> str | None:
        cal, drive = self._require_services(pid)
        if cal is None or drive is None:
            return None

        filename = f'[{subject}] {_safe_filename(title)}.md'
        body = (
            f'Название: {title}\n'
            f'Предмет: {subject}\n'
            f'Дата: {dt.datetime.now().strftime("%d.%m.%Y")}\n\n'
            f'Конспект:\n{content}'
        )

        file_id, link = drive.upload_note(filename, body)

        subj_row = self.db.get_subject_by_name(pid, subject)
        if subj_row:
            self.db.add_note(subj_row[0], title, content[:1000], link)

        return link


    def send(self, pid, text, kb=None):
        params = {
            'peer_id': pid,
            'message': text,
            'random_id': get_random_id()
        }
        if kb is not None:
            params['keyboard'] = kb.get_keyboard()
        self.vk.method('messages.send', params)

    def send_main(self, pid, text='Главное меню:'):
        self.db.set_state(pid, 'main_menu')
        self.db.set_temp(pid, None)
        self.send(pid, text, V.kb_main())

    def _show_subjects(self, pid, text=None):
        subs = self.db.get_all_subjects(pid)
        self.db.set_state(pid, 'selecting_subject')
        self.send(pid, text or ('Ваши предметы:' if subs else 'Предметов ещё нет.'), V.kb_subjects(subs))

    def handle_route(self, pid: int, text: str, payload: dict | None) -> None:
        state = self.db.get_state(pid) or 'main_menu'
        payload_state = self._payload_state(payload)

        if payload_state == 'cancel' and not state.startswith('date_'):
            self.send_main(pid, 'Отменено.')
            return

        handler = getattr(self, f'st_{state}', None)

        if handler is None:
            self.send_main(pid)
            return

        try:
            handler(pid, text, payload or {})
        except Exception as e:
            self.send(pid, f'❌ Ошибка: {e}', V.kb_main())
            self.send_main(pid)

    def handle(self, pid: int, text: str, payload: dict | None, docs: list[dict] | None = None) -> None:
        self._docs = docs or []
        if self.db.get_creds(pid) is None:
            from auth_server import auth_url_for
            self.send(
                pid,
                'Чтобы бот мог писать в твой Google Календарь и Drive, '
                f'авторизуйся: {auth_url_for(pid)}'
            )
            return
        self.handle_route(pid, text, payload)


    def st_main_menu(self, pid: int, text: str, payload: dict) -> None:
        payload_state = self._payload_state(payload)

        if payload_state == 'subjects':
            self._show_subjects(pid, 'Выбор предметов:')
            return

        if payload_state == 'upcoming':
            self.show_upcoming(pid)
            return

        if payload_state == 'ai_chat':
            self.db.set_state(pid, 'ai_chat')
            self.send(pid, 'AI-чат включён. Напишите сообщение. Для выхода нажмите «Отмена».', V.kb_cancel())
            return

        self.send(pid, 'Пользуйся кнопками.', V.kb_main())

    def st_selecting_subject(self, pid: int, text: str, payload: dict) -> None:
        payload_state = self._payload_state(payload)
        value = self._payload_value(payload)

        if payload_state == 'add_subject':
            self.db.set_state(pid, 'wait_new_subject')
            self.send(pid, 'Напишите название нового предмета.', V.kb_cancel())
            return

        if payload_state == 'pick_subject' and value:
            self.db.set_cur_subject(pid, value)
            self.db.set_state(pid, 'subject_menu')
            self.send(pid, f'Выбран предмет: {value}', V.kb_subject_actions())
            return

        self._show_subjects(pid)

    def st_wait_new_subject(self, pid: int, text: str, payload: dict) -> None:
        if self._payload_state(payload) is not None:
            self.send(pid, 'Напишите название предмета текстом.', V.kb_cancel())
            return

        name = (text or '').strip()
        if not name:
            self.send(pid, 'Нужен текст.', V.kb_cancel())
            return

        if len(name) > 40:
            self.send(pid, 'Слишком длинное название (макс. 40 символов). Сократите.', V.kb_cancel())
            return

        added = self.db.add_subject(pid, name)
        if added:
            self._show_subjects(pid, f'Добавлен предмет «{name}».')
        else:
            self._show_subjects(pid, f'Предмет «{name}» уже существует.')

    def st_subject_menu(self, pid: int, text: str, payload: dict) -> None:
        subj = self.db.get_cur_subject(pid)
        if not subj:
            self._show_subjects(pid)
            return

        payload_state = self._payload_state(payload)

        if payload_state == 'write_note':
            self.db.set_state(pid, 'wait_note_title')
            self._set_temp(pid, {})
            self.send(pid, 'Название конспекта:', V.kb_cancel())
            return

        if payload_state == 'search':
            self.db.set_state(pid, 'search_menu')
            self.send(pid, 'Как ищем?', V.kb_search())
            return

        if payload_state == 'load_note':
            self.db.set_state(pid, 'wait_load_file')
            self.send(pid, 'Прикрепите текстовый файл (.txt, .md и т.п.) к сообщению.', V.kb_cancel())
            return

        if payload_state == 'back_subjects':
            self._show_subjects(pid)
            return

        if payload_state == 'delete_subject':
            self.db.remove_subject(pid, subj)
            self._show_subjects(pid, f'Предмет «{subj}» удалён.')
            return

        self.send(pid, f'Выбран предмет: {subj}', V.kb_subject_actions())

    def st_wait_load_file(self, pid: int, text: str, payload: dict) -> None:
        if self._payload_state(payload) is not None:
            self.send(pid, 'Прикрепите файл к сообщению.', V.kb_cancel())
            return

        subj = self.db.get_cur_subject(pid)
        if not subj:
            self._show_subjects(pid, 'Сначала выберите предмет.')
            return

        if not self._docs:
            self.send(pid, 'Нет вложенного файла. Прикрепите текстовый файл к сообщению.', V.kb_cancel())
            return

        result = self._download_doc(self._docs[0])
        if result is None:
            self.send(
                pid,
                'Не удалось прочитать файл. Поддерживаются текстовые файлы до 2 МБ '
                '(txt, md, csv, json, xml, html, py и др.).',
                V.kb_cancel()
            )
            return

        title, content = result
        if len(title) > 60:
            title = title[:57] + '...'

        if not content.strip():
            self.send(pid, 'Файл пуст.', V.kb_cancel())
            return

        link = self._save_note_to_drive(pid, subj, title, content)
        if link is None:
            return

        self._set_temp(pid, {'title': title, 'link': link})
        self.db.set_state(pid, 'ask_reminder')
        self.send(pid, f'✅ Сохранено на Drive:\n{link}\n\nПоставить напоминание в календаре?', V.kb_yes_no_reminder())


    def st_wait_note_title(self, pid: int, text: str, payload: dict) -> None:
        if self._payload_state(payload) is not None:
            self.send(pid, 'Пришлите название конспекта текстом.', V.kb_cancel())
            return

        title = (text or '').strip()
        if not title:
            self.send(pid, 'Нужен текст.', V.kb_cancel())
            return

        temp = self._get_temp(pid)
        temp['title'] = title
        self._set_temp(pid, temp)
        self.db.set_state(pid, 'wait_note_text')
        self.send(pid, f'Название: «{title}».\nТеперь пришлите текст конспекта:', V.kb_cancel())

    def st_wait_note_text(self, pid: int, text: str, payload: dict) -> None:
        if self._payload_state(payload) is not None:
            self.send(pid, 'Пришлите текст конспекта сообщением.', V.kb_cancel())
            return

        note_text = (text or '').strip()
        if not note_text:
            self.send(pid, 'Нужен текст.', V.kb_cancel())
            return

        subj = self.db.get_cur_subject(pid)
        if not subj:
            self._show_subjects(pid, 'Сначала выберите предмет.')
            return

        temp = self._get_temp(pid)
        title = (temp.get('title') or 'Без названия').strip()

        link = self._save_note_to_drive(pid, subj, title, note_text)
        if link is None:
            return

        cal, drive = self._services(pid)
        file_id = None
        try:
            notes = drive.list_notes(subject=subj)
            if notes:
                file_id = notes[0].get('id')
        except Exception:
            pass

        self._set_temp(pid, {'title': title, 'file_id': file_id, 'link': link})
        self.db.set_state(pid, 'ask_reminder')
        self.send(pid, f'✅ Сохранено на Drive:\n{link}\n\nПоставить напоминание в календаре?', V.kb_yes_no_reminder())

    def st_ask_reminder(self, pid: int, text: str, payload: dict) -> None:
        payload_state = self._payload_state(payload)

        if payload_state == 'rem_yes':
            self.db.set_state(pid, 'date_year')
            self.send(pid, 'Выбери год:', V.kb_years())
            return

        if payload_state == 'rem_no':
            self.send_main(pid, 'Готово. Без напоминания.')
            return

        self.send(pid, 'Выбери Да или Нет.', V.kb_yes_no_reminder())

    def st_ai_chat(self, pid: int, text: str, payload: dict) -> None:
        if self._payload_state(payload) == 'cancel':
            self.send_main(pid, 'Выход из AI-чата.')
            return

        file_text = ''
        file_title = ''
        if self._docs:
            result = self._download_doc(self._docs[0])
            if result:
                file_title, file_text = result

        combined_text = text or ''
        if file_text:
            label = f'[Файл: {file_title}]\n{file_text}'
            combined_text = f'{combined_text}\n\n{label}'.strip() if combined_text else label

        if not combined_text:
            self.send(pid, 'Напишите сообщение или прикрепите файл.', V.kb_cancel())
            return

        if not ai_on():
            self.send(pid, 'AI сейчас не настроен.', V.kb_cancel())
            return

        pending = self.db.get_pending_action(pid)
        if pending and combined_text.strip().lower() in ('да', 'yes', 'ок', 'ok', 'давай', 'сохрани', 'сохранить',
                                                         'создай', 'создать'):
            self.db.set_state(pid, 'ai_confirm_action')
            self.st_ai_confirm_action(pid, '', {'state': 'ai_confirm'})
            return

        history = self.db.get_ai_history(pid)
        persona = self.db.get_ai_persona(pid)
        subj = self.db.get_cur_subject(pid)
        subs = self.db.get_all_subjects(pid)

        turn = chat_turn(
            combined_text,
            history,
            persona=persona,
            subjects=subs,
            current_subject=subj
        )

        reply = (turn.get('reply') or '').strip() or 'Не удалось сформировать ответ.'
        action = turn.get('action')

        new_history = history + [
            {'role': 'user', 'content': combined_text},
            {'role': 'assistant', 'content': reply}
        ]
        self.db.set_ai_history(pid, trim_history(new_history))

        if not action:
            self.send(pid, reply, V.kb_cancel())
            return

        intent = action.get('intent', '')
        confidence = float(action.get('confidence', 0) or 0)
        missing = action.get('missing') or []

        if intent == 'show_upcoming' and confidence >= 0.6:
            if reply:
                self.send(pid, reply)
            self.show_upcoming(
                pid,
                keep_state='ai_chat',
                final_text='Можете написать ещё сообщение.',
                final_kb=V.kb_cancel()
            )
            return

        if intent in ('save_summary', 'create_reminder'):
            if missing or confidence < 0.6:
                self.db.set_pending_action(pid, action)
                self.send(pid, reply, V.kb_cancel())
                return
            self.db.set_pending_action(pid, action)
            self.db.set_state(pid, 'ai_confirm_action')
            self.send(pid, reply, V.kb_ai_confirm())
            return

        self.send(pid, reply, V.kb_cancel())

    # ─────────────────────────────────────────────
    # ЗАМЕНИТЬ st_ai_confirm_action целиком
    # ─────────────────────────────────────────────
    def st_ai_confirm_action(self, pid: int, text: str, payload: dict) -> None:
        payload_state = self._payload_state(payload)

        if payload_state == 'ai_reject':
            self.db.set_pending_action(pid, None)
            self.db.set_state(pid, 'ai_chat')
            self.send(pid, 'Хорошо, ничего не сохраняю.', V.kb_cancel())
            return

        if payload_state == 'cancel':
            self.db.set_pending_action(pid, None)
            self.send_main(pid, 'Отменено.')
            return

        if payload_state != 'ai_confirm':
            self.send(pid, 'Нажмите «Подтвердить» или «Не сохранять».', V.kb_ai_confirm())
            return

        action = self.db.get_pending_action(pid)
        if not action:
            self.db.set_state(pid, 'ai_chat')
            self.send(pid, 'Действие не найдено, попробуйте ещё раз.', V.kb_cancel())
            return

        intent = action.get('intent', '')
        subj = self.db.get_cur_subject(pid)

        try:
            if intent == 'save_summary':
                self._execute_ai_save_summary(pid, action, subj)
            elif intent == 'create_reminder':
                self._execute_ai_reminder(pid, action, subj)
            else:
                self.db.set_pending_action(pid, None)
                self.db.set_state(pid, 'ai_chat')
                self.send(pid, 'Неизвестное действие.', V.kb_cancel())
        except Exception as e:
            self.db.set_pending_action(pid, None)
            self.db.set_state(pid, 'ai_chat')
            self.send(pid, f'❌ Ошибка при выполнении: {e}', V.kb_cancel())

    def _execute_ai_save_summary(self, pid: int, action: dict, current_subject: str | None) -> None:
        cal, drive = self._require_services(pid)
        if cal is None or drive is None:
            return

        subject = (action.get('subject') or current_subject or '').strip()
        if not subject:
            self.db.set_pending_action(pid, None)
            self.db.set_state(pid, 'ai_chat')
            self.send(
                pid,
                'Не удалось определить предмет. Напишите его или выберите через меню «Предметы».',
                V.kb_cancel()
            )
            return

        content = (action.get('content') or '').strip()
        if not content:
            self.db.set_pending_action(pid, None)
            self.db.set_state(pid, 'ai_chat')
            self.send(pid, 'Не удалось найти текст конспекта. Пришлите текст для сохранения.', V.kb_cancel())
            return

        subs = self.db.get_all_subjects(pid)
        subject_map = {s.casefold(): s for s in subs}
        real_subject = subject_map.get(subject.casefold(), subject)

        if real_subject.casefold() not in subject_map:
            if len(real_subject) > 40:
                real_subject = real_subject[:40]
            self.db.add_subject(pid, real_subject)

        self.db.set_cur_subject(pid, real_subject)

        title = (action.get('title') or 'Конспект').strip()

        link = self._save_note_to_drive(pid, real_subject, title, content)
        if link is None:
            return

        self.db.set_pending_action(pid, None)
        self.db.set_state(pid, 'ai_chat')
        self.send(pid, f'✅ Конспект сохранён на Drive:\n{link}', V.kb_cancel())

    # ─────────────────────────────────────────────
    # ЗАМЕНИТЬ _execute_ai_reminder целиком
    # ─────────────────────────────────────────────
    def _execute_ai_reminder(self, pid: int, action: dict, current_subject: str | None) -> None:
        cal, drive = self._require_services(pid)
        if cal is None or drive is None:
            return

        when_text = (action.get('when_text') or '').strip()
        if not when_text:
            self.db.set_pending_action(pid, None)
            self.db.set_state(pid, 'ai_chat')
            self.send(pid, 'Не удалось определить дату и время. Напишите, например: завтра в 19:00.', V.kb_cancel())
            return

        start = dateparser.parse(
            when_text,
            languages=['ru'],
            settings={
                'TIMEZONE': 'Europe/Samara',
                'RETURN_AS_TIMEZONE_AWARE': False,
                'PREFER_DATES_FROM': 'future'
            }
        )

        if not start:
            self.db.set_pending_action(pid, None)
            self.db.set_state(pid, 'ai_chat')
            self.send(
                pid,
                f'Не удалось разобрать дату «{when_text}».\n'
                'Напишите точнее, например: 25.05.2026 18:30 или послезавтра в 14:00.',
                V.kb_cancel()
            )
            return

        # Защита от дат в прошлом
        if start < dt.datetime.now():
            self.db.set_pending_action(pid, None)
            self.db.set_state(pid, 'ai_chat')
            self.send(
                pid,
                f'Дата {start:%d.%m.%Y %H:%M} уже в прошлом. Укажите будущую дату.',
                V.kb_cancel()
            )
            return

        end = start + dt.timedelta(hours=1)
        subject = (action.get('subject') or current_subject or '').strip()
        title = (action.get('title') or 'Напоминание').strip()
        summary = f'[{subject}] {title}' if subject else title
        description, drive_file_id = self._build_reminder_description_from_note(pid, subject)

        ev = cal.add_event(
            summary,
            start,
            end,
            description=description,
            drive_file_id=drive_file_id
        )

        self.db.set_pending_action(pid, None)
        self.db.set_state(pid, 'ai_chat')
        self.send(
            pid,
            f'✅ Напоминание создано:\n'
            f'Название: {summary}\n'
            f'Когда: {start:%d.%m.%Y %H:%M}\n'
            f'{ev.get("htmlLink", "")}\n\n'
            f'Можете продолжить диалог.',
            V.kb_cancel()
        )

    def _find_latest_note(self, pid: int, subject: str):
        if not subject:
            return None

        cal, drive = self._require_services(pid)
        if cal is None or drive is None:
            return None

        notes = drive.list_notes(subject=subject)
        return notes[0] if notes else None

    def _build_reminder_description_from_note(self, pid: int, subject: str = '', *, file_id: str | None = None, link: str = ''):
        cal, drive = self._require_services(pid)
        if cal is None or drive is None:
            return '', None

        note_file_id = file_id
        note_link = (link or '').strip()

        if not note_file_id:
            note = self._find_latest_note(pid, subject)
            if note:
                note_file_id = note.get('id')
                note_link = note.get('webViewLink', '') or note_link

        if not note_file_id:
            return '', None

        try:
            raw = drive.download_text(note_file_id)
            note_summary = summarize(raw, max_chars=1200).strip()
        except Exception:
            note_summary = ''

        parts = []
        if note_summary:
            parts.append(note_summary)
        if note_link:
            parts.append(note_link)

        return '\n\n'.join(parts).strip(), note_file_id

    def st_date_year(self, pid: int, text: str, payload: dict) -> None:
        payload_state = self._payload_state(payload)
        value = self._payload_value(payload)

        if payload_state == 'cancel':
            self.send_main(pid, 'Отменено.')
            return

        if payload_state == 'year' and value is not None:
            self._date_set(pid, 'year', value)
            self.db.set_state(pid, 'date_month')
            self.send(pid, f'Год: {int(value)}\nМесяц:', V.kb_months())
            return

        self.send(pid, 'Выбери год.', V.kb_years())

    def st_date_month(self, pid: int, text: str, payload: dict) -> None:
        payload_state = self._payload_state(payload)
        value = self._payload_value(payload)

        if payload_state == 'cancel':
            self.send_main(pid, 'Отменено.')
            return

        if payload_state == 'back':
            self.db.set_state(pid, 'date_year')
            self.send(pid, 'Год:', V.kb_years())
            return

        if payload_state == 'month' and value is not None:
            self._date_set(pid, 'month', value)
            temp = self._get_temp(pid)
            year = temp.get('year')
            if not year:
                self.db.set_state(pid, 'date_year')
                self.send(pid, 'Сначала выбери год.', V.kb_years())
                return
            self.db.set_state(pid, 'date_day')
            self.send(pid, f'{year}-{int(value):02d}\nДень:', V.kb_days(year, int(value)))
            return

        self.send(pid, 'Выбери месяц.', V.kb_months())

    def st_date_day(self, pid: int, text: str, payload: dict) -> None:
        payload_state = self._payload_state(payload)
        value = self._payload_value(payload)
        temp = self._get_temp(pid)

        if payload_state == 'cancel':
            self.send_main(pid, 'Отменено.')
            return

        if payload_state == 'back':
            self.db.set_state(pid, 'date_month')
            self.send(pid, 'Месяц:', V.kb_months())
            return

        if payload_state == 'day' and value is not None:
            self._date_set(pid, 'day', value)
            self.db.set_state(pid, 'date_hour')
            self.send(pid, 'Час:', V.kb_hours())
            return

        year = temp.get('year')
        month = temp.get('month')
        if not year or not month:
            self.db.set_state(pid, 'date_month')
            self.send(pid, 'Сначала выбери месяц.', V.kb_months())
            return

        self.send(pid, 'Выбери день.', V.kb_days(year, month))

    def st_date_hour(self, pid: int, text: str, payload: dict) -> None:
        payload_state = self._payload_state(payload)
        value = self._payload_value(payload)
        temp = self._get_temp(pid)

        if payload_state == 'cancel':
            self.send_main(pid, 'Отменено.')
            return

        if payload_state == 'back':
            self.db.set_state(pid, 'date_day')
            year = temp.get('year')
            month = temp.get('month')
            if not year or not month:
                self.db.set_state(pid, 'date_month')
                self.send(pid, 'Сначала выбери месяц.', V.kb_months())
                return
            self.send(pid, 'День:', V.kb_days(year, month))
            return

        if payload_state == 'hour' and value is not None:
            self._date_set(pid, 'hour', value)
            self.db.set_state(pid, 'date_minute')
            self.send(pid, 'Минуты:', V.kb_minutes())
            return

        self.send(pid, 'Выбери час.', V.kb_hours())

    def st_date_minute(self, pid: int, text: str, payload: dict) -> None:
        payload_state = self._payload_state(payload)
        value = self._payload_value(payload)

        if payload_state == 'cancel':
            self.send_main(pid, 'Отменено.')
            return

        if payload_state == 'back':
            self.db.set_state(pid, 'date_hour')
            self.send(pid, 'Час:', V.kb_hours())
            return

        if payload_state == 'minute' and value is not None:
            self._date_set(pid, 'minute', value)
            self._create_reminder(pid)
            return

        self.send(pid, 'Выбери минуты.', V.kb_minutes())

    def _date_set(self, pid: int, key: str, val) -> None:
        temp = self._get_temp(pid)
        temp[key] = int(val)
        self._set_temp(pid, temp)

    def _create_reminder(self, pid: int) -> None:
        cal, drive = self._require_services(pid)
        if cal is None or drive is None:
            return

        temp = self._get_temp(pid)
        subject = self.db.get_cur_subject(pid) or ''
        title = temp.get('title', 'Конспект')
        file_id = temp.get('file_id')
        link = temp.get('link', '')

        try:
            start = dt.datetime(
                int(temp['year']),
                int(temp['month']),
                int(temp['day']),
                int(temp['hour']),
                int(temp['minute'])
            )
        except Exception:
            self.db.set_state(pid, 'date_year')
            self.send(pid, 'Не удалось собрать дату. Выбери год заново.', V.kb_years())
            return

        end = start + dt.timedelta(hours=1)
        summary = f'[{subject}] {title}' if subject else title
        description, drive_file_id = self._build_reminder_description_from_note(
            pid,
            subject,
            file_id=file_id,
            link=link
        )

        ev = cal.add_event(
            summary,
            start,
            end,
            description=description,
            drive_file_id=drive_file_id
        )

        self.send_main(
            pid,
            f'✅ Напоминание создано на {start:%d.%m.%Y %H:%M}\n{ev.get("htmlLink", "")}'
        )

    def st_search_menu(self, pid: int, text: str, payload: dict) -> None:
        subj = self.db.get_cur_subject(pid)
        if not subj:
            self._show_subjects(pid, 'Сначала выберите предмет.')
            return

        payload_state = self._payload_state(payload)

        if payload_state == 'search_all':
            self._show_search(pid, subj, 'all', '')
            return

        if payload_state == 'search_title':
            self.db.set_state(pid, 'wait_search_title')
            self.send(pid, 'Слово из названия:', V.kb_cancel())
            return

        if payload_state == 'search_date':
            self.db.set_state(pid, 'wait_search_date')
            self.send(pid, 'Дата в формате ДД.ММ.ГГГГ:', V.kb_cancel())
            return

        self.send(pid, 'Используй кнопки.', V.kb_search())

    def st_wait_search_title(self, pid: int, text: str, payload: dict) -> None:
        if self._payload_state(payload) is not None:
            self.send(pid, 'Нужен текст для поиска.', V.kb_cancel())
            return

        query = (text or '').strip()
        if not query:
            self.send(pid, 'Нужен текст.', V.kb_cancel())
            return

        subj = self.db.get_cur_subject(pid)
        if not subj:
            self._show_subjects(pid, 'Сначала выберите предмет.')
            return

        self._show_search(pid, subj, 'title', query)

    def st_wait_search_date(self, pid: int, text: str, payload: dict) -> None:
        if self._payload_state(payload) is not None:
            self.send(pid, 'Нужен текст для поиска.', V.kb_cancel())
            return

        query = (text or '').strip()
        if not query:
            self.send(pid, 'Нужен текст.', V.kb_cancel())
            return

        subj = self.db.get_cur_subject(pid)
        if not subj:
            self._show_subjects(pid, 'Сначала выберите предмет.')
            return

        self._show_search(pid, subj, 'date', query)

    def _show_search(self, pid: int, subject: str, kind: str, query: str) -> None:
        cal, drive = self._require_services(pid)
        if cal is None or drive is None:
            return

        notes = drive.list_notes(subject=subject)
        results = []

        for note in notes:
            try:
                name = note.get('name', '')
                if kind == 'title' and query.lower() not in name.lower():
                    continue

                if kind == 'date':
                    content = drive.download_text(note['id'])
                    if f'Дата: {query}' not in content:
                        continue

                clean = name.replace(f'[{subject}] ', '').replace('.md', '')
                results.append(f'📄 {clean}\n{note.get("webViewLink", "")}')
            except Exception:
                continue

        if not results:
            txt = 'Ничего не найдено.'
        else:
            txt = f'Найдено ({len(results)}):\n\n' + '\n\n'.join(results[:10])
            if len(results) > 10:
                txt += f'\n\n…ещё {len(results) - 10}'

        self.db.set_state(pid, 'subject_menu')
        self.send(pid, txt, V.kb_subject_actions())

    def show_upcoming(self, pid, *, keep_state='main_menu', final_text='Главное меню:', final_kb=None):
        try:
            cal, drive = self._require_services(pid)
            if cal is None or drive is None:
                return
            events = cal.list_upcoming(days=7)
        except Exception as e:
            self.db.set_state(pid, keep_state)
            self.send(pid, f'Не удалось получить события: {e}', final_kb or V.kb_main())
            return

        if not events:
            self.db.set_state(pid, keep_state)
            self.send(pid, 'Ближайших напоминаний нет.', final_kb or V.kb_main())
            return

        shown = 0

        for ev in events[:5]:
            try:
                start = ev.get('start', {}).get('dateTime') or ev.get('start', {}).get('date') or ''
                summary = ev.get('summary', 'Без названия')
                description = (ev.get('description') or '').strip()

                msg = f'⏰ {summary}\n🕒 {start}'
                if description:
                    msg += f'\n\n{description}'

                self.send(pid, msg)
                shown += 1
            except Exception as e:
                self.send(pid, f'⏰ {ev.get("summary", "Без названия")}\n\n(не удалось прочитать событие: {e})')

        self.db.set_state(pid, keep_state)

        if shown == 0:
            self.send(pid, 'Ближайших напоминаний нет.', final_kb or V.kb_main())
            return

        self.send(pid, final_text, final_kb or V.kb_main())
