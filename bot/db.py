import sqlite3
import json
from typing import Any


class Database:
    def __init__(self, db_path='bot_database.db'):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute('PRAGMA foreign_keys = ON')
        self.cur = self.conn.cursor()
        self._init()

    def _init(self):
        self.cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                state TEXT DEFAULT 'main_menu',
                current_subject TEXT,
                subjects TEXT DEFAULT '[]',
                temp_data TEXT,
                google_creds TEXT
            )
        ''')

        self.cur.execute('''
            CREATE TABLE IF NOT EXISTS subjects (
                subject_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                subject_name TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
            )
        ''')

        self.cur.execute('''
            CREATE TABLE IF NOT EXISTS notes (
                note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject_id INTEGER NOT NULL,
                note_name TEXT NOT NULL,
                summary TEXT CHECK (length(summary) <= 1000),
                drive_link TEXT,
                FOREIGN KEY (subject_id) REFERENCES subjects (subject_id) ON DELETE CASCADE
            )
        ''')

        self.cur.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_subjects_user_name
            ON subjects(user_id, subject_name)
        ''')

        cols = {r[1] for r in self.cur.execute("PRAGMA table_info(users)").fetchall()}

        if 'google_creds' not in cols:
            self.cur.execute('ALTER TABLE users ADD COLUMN google_creds TEXT')
        if 'ai_persona' not in cols:
            self.cur.execute("ALTER TABLE users ADD COLUMN ai_persona TEXT DEFAULT ''")
        if 'ai_history' not in cols:
            self.cur.execute("ALTER TABLE users ADD COLUMN ai_history TEXT DEFAULT '[]'")
        if 'pending_action' not in cols:
            self.cur.execute("ALTER TABLE users ADD COLUMN pending_action TEXT")

        self.conn.commit()

    def _ensure_user(self, user_id: int) -> None:
        self.cur.execute('INSERT OR IGNORE INTO users(user_id) VALUES (?)', (user_id,))

    def get_creds(self, user_id: int) -> str | None:
        self.cur.execute('SELECT google_creds FROM users WHERE user_id=?', (user_id,))
        row = self.cur.fetchone()
        return row[0] if row and row[0] else None

    def set_creds(self, user_id: int, creds_json: str) -> None:
        self._ensure_user(user_id)
        self.cur.execute('UPDATE users SET google_creds=? WHERE user_id=?', (creds_json, user_id))
        self.conn.commit()

    def get_state(self, user_id: int) -> str:
        self.cur.execute('SELECT state FROM users WHERE user_id=?', (user_id,))
        row = self.cur.fetchone()
        return row[0] if row and row[0] else 'main_menu'

    def set_state(self, user_id: int, state: str) -> None:
        self._ensure_user(user_id)
        self.cur.execute('UPDATE users SET state=? WHERE user_id=?', (state, user_id))
        self.conn.commit()

    def set_cur_subject(self, user_id: int, subject: str | None) -> None:
        self._ensure_user(user_id)
        self.cur.execute('UPDATE users SET current_subject=? WHERE user_id=?', (subject, user_id))
        self.conn.commit()

    def get_cur_subject(self, user_id: int) -> str | None:
        self.cur.execute('SELECT current_subject FROM users WHERE user_id=?', (user_id,))
        row = self.cur.fetchone()
        return row[0] if row and row[0] else None

    def add_subject(self, user_id: int, name: str) -> bool:
        name = (name or '').strip()
        if not name:
            return False

        self._ensure_user(user_id)

        self.cur.execute(
            'SELECT subject_id FROM subjects WHERE user_id=? AND subject_name=?',
            (user_id, name)
        )
        if self.cur.fetchone():
            return False

        self.cur.execute(
            'INSERT INTO subjects (user_id, subject_name) VALUES (?, ?)',
            (user_id, name)
        )
        self.conn.commit()
        return True

    def get_subject_by_name(self, user_id: int, name: str):
        self.cur.execute(
            'SELECT subject_id, user_id, subject_name FROM subjects WHERE user_id=? AND subject_name=?',
            (user_id, name)
        )
        return self.cur.fetchone()

    def get_all_subjects(self, user_id: int) -> list[str]:
        self.cur.execute(
            'SELECT subject_name FROM subjects WHERE user_id=? ORDER BY subject_name COLLATE NOCASE',
            (user_id,)
        )
        return [row[0] for row in self.cur.fetchall()]

    def remove_subject(self, user_id: int, name: str) -> bool:
        self.cur.execute('DELETE FROM subjects WHERE user_id=? AND subject_name=?', (user_id, name))
        deleted = self.cur.rowcount > 0

        if deleted and self.get_cur_subject(user_id) == name:
            self.cur.execute('UPDATE users SET current_subject=NULL WHERE user_id=?', (user_id,))

        self.conn.commit()
        return deleted

    def add_note(self, subject_id: int, name: str, summary: str, link: str) -> None:
        self.cur.execute(
            'INSERT INTO notes (subject_id, note_name, summary, drive_link) VALUES (?, ?, ?, ?)',
            (subject_id, name, (summary or '')[:1000], link)
        )
        self.conn.commit()

    def remove_note(self, subject_id: int, name: str) -> None:
        self.cur.execute('DELETE FROM notes WHERE subject_id=? AND note_name=?', (subject_id, name))
        self.conn.commit()

    def get_all_notes(self, subject_id: int) -> list:
        self.cur.execute('SELECT * FROM notes WHERE subject_id=?', (subject_id,))
        return self.cur.fetchall()

    def search_note_by_name(self, subject_id: int, name: str) -> list:
        self.cur.execute(
            'SELECT * FROM notes WHERE subject_id=? AND note_name LIKE ?',
            (subject_id, f'%{name}%')
        )
        return self.cur.fetchall()

    def set_temp(self, user_id: int, data: dict[str, Any] | None) -> None:
        self._ensure_user(user_id)
        val = json.dumps(data, ensure_ascii=False) if data is not None else None
        self.cur.execute('UPDATE users SET temp_data=? WHERE user_id=?', (val, user_id))
        self.conn.commit()

    def get_temp(self, user_id: int) -> str | None:
        self.cur.execute('SELECT temp_data FROM users WHERE user_id=?', (user_id,))
        row = self.cur.fetchone()
        return row[0] if row else None

    def get_user(self, user_id: int):
        self.cur.execute(
            'SELECT state, current_subject, temp_data FROM users WHERE user_id=?',
            (user_id,)
        )
        row = self.cur.fetchone()
        if row is None:
            return 'main_menu', None, [], {}

        state, current_subject, temp_data = row
        subjects = self.get_all_subjects(user_id)
        temp = json.loads(temp_data) if temp_data else {}
        return state or 'main_menu', current_subject, subjects, temp

    def get_ai_history(self, user_id: int) -> list[dict]:
        self.cur.execute('SELECT ai_history FROM users WHERE user_id=?', (user_id,))
        row = self.cur.fetchone()
        return json.loads(row[0]) if row and row[0] else []

    def set_ai_history(self, user_id: int, history: list[dict]) -> None:
        self._ensure_user(user_id)
        self.cur.execute(
            'UPDATE users SET ai_history=? WHERE user_id=?',
            (json.dumps(history, ensure_ascii=False), user_id)
        )
        self.conn.commit()

    def get_ai_persona(self, user_id: int) -> str:
        self.cur.execute('SELECT ai_persona FROM users WHERE user_id=?', (user_id,))
        row = self.cur.fetchone()
        return row[0] if row and row[0] else ''

    def set_ai_persona(self, user_id: int, persona: str) -> None:
        self._ensure_user(user_id)
        self.cur.execute('UPDATE users SET ai_persona=? WHERE user_id=?', (persona, user_id))
        self.conn.commit()

    def get_pending_action(self, user_id: int):
        self.cur.execute('SELECT pending_action FROM users WHERE user_id=?', (user_id,))
        row = self.cur.fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def set_pending_action(self, user_id: int, data) -> None:
        self._ensure_user(user_id)
        val = json.dumps(data, ensure_ascii=False) if data is not None else None
        self.cur.execute('UPDATE users SET pending_action=? WHERE user_id=?', (val, user_id))
        self.conn.commit()
