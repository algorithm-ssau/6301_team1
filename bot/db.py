import sqlite3
import json
from typing import Any

class Database:
    def __init__(self, db_path='bot_database.db'):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cur = self.conn.cursor()
        self._init()

    def _init(self):
        self.cur.execute('''
            CREATE TABLE IF NOT EXISTS users 
            (
                user_id INTEGER PRIMARY KEY,
                state TEXT DEFAULT 'main_menu',
                current_subject TEXT,
                subjects TEXT DEFAULT '[]',
                temp_data TEXT,
                google_creds TEXT
            )
        ''')

        # для уже существующей БД
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

    def get_creds(self, user_id: int) -> str | None:
        self.cur.execute('SELECT google_creds FROM users WHERE user_id=?', (user_id,))
        row = self.cur.fetchone()
        return row[0] if row and row[0] else None

    def set_creds(self, user_id: int, creds_json: str):
        self.cur.execute('INSERT OR IGNORE INTO users(user_id) VALUES (?)', (user_id,))
        self.cur.execute('UPDATE users SET google_creds=? WHERE user_id=?',
                         (creds_json, user_id))
        self.conn.commit()

    def get_user(self, user_id: int):
        self.cur.execute(
            'SELECT state,current_subject,subjects,temp_data FROM users WHERE user_id=?',
            (user_id,))
        row = self.cur.fetchone()
        if row is None:
            self.cur.execute('INSERT INTO users(user_id) VALUES (?)', (user_id,))
            self.conn.commit()
            return 'main_menu', None, [], {}
        state, subj, subs, td = row
        return state, subj, json.loads(subs or '[]'), json.loads(td) if td else {}

    def set_state(self, user_id: int, state: str):
        self.cur.execute('UPDATE users SET state=? WHERE user_id=?', (state, user_id))
        self.conn.commit()

    def set_subject(self, user_id: int, subject):
        self.cur.execute('UPDATE users SET current_subject=? WHERE user_id=?',
                         (subject, user_id))
        self.conn.commit()

    def set_temp(self, user_id: int, data: dict[str, Any] | None):
        val = json.dumps(data, ensure_ascii=False) if data else None
        self.cur.execute('UPDATE users SET temp_data=? WHERE user_id=?', (val, user_id))
        self.conn.commit()

    def add_subject(self, user_id: int, name: str) -> bool:
        _, _, subs, _ = self.get_user(user_id)
        if name in subs:
            return False
        subs.append(name)
        self.cur.execute('UPDATE users SET subjects=? WHERE user_id=?',
                         (json.dumps(subs, ensure_ascii=False), user_id))
        self.conn.commit()
        return True

    def get_ai_history(self, user_id: int) -> list[dict]:
        self.cur.execute('SELECT ai_history FROM users WHERE user_id=?', (user_id,))
        row = self.cur.fetchone()
        return json.loads(row[0]) if row and row[0] else []
