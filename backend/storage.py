"""SQLite 只保存主动选择保留的会话；临时会话由 API 保存在内存。"""
import json
import sqlite3
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
            db.execute('''CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY, title TEXT, url TEXT, version TEXT,
                content TEXT, active INTEGER, builtin INTEGER, filename TEXT)''')

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def save_session(self, session):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?)',
                       (session['id'], json.dumps(session, ensure_ascii=False)))

    def get_session(self, key):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM sessions WHERE id=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def sessions(self):
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM sessions').fetchall()
        return [json.loads(row[0]) for row in rows]

    def delete_session(self, key):
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE id=?', (key,))

    def documents(self, active_only=False):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute('SELECT * FROM documents' + (' WHERE active=1' if active_only else '')).fetchall()
        return [dict(r) for r in rows]

    def put_document(self, doc):
        keys = ('id', 'title', 'url', 'version', 'content', 'active', 'builtin', 'filename')
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?,?,?)', [doc[k] for k in keys])

    def set_active(self, key, active):
        with self.connect() as db:
            return db.execute('UPDATE documents SET active=? WHERE id=?', (int(active), key)).rowcount

    def delete_document(self, key):
        with self.connect() as db:
            return db.execute('DELETE FROM documents WHERE id=?', (key,)).rowcount

    def initialized(self):
        with self.connect() as db:
            return bool(db.execute("SELECT 1 FROM settings WHERE key='seeded'").fetchone())

    def mark_initialized(self):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('seeded', '1')")
