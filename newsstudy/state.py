import sqlite3
from pathlib import Path
from .settings import now

class State:
    def __init__(self, root):
        p = Path(root) / 'data/state.sqlite3'
        p.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(p)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS days (
            day TEXT PRIMARY KEY, status TEXT NOT NULL, error TEXT DEFAULT '',
            pdf TEXT DEFAULT '', url TEXT DEFAULT '', expires TEXT DEFAULT '',
            notified INTEGER DEFAULT 0, updated TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY, days TEXT NOT NULL, status TEXT NOT NULL, updated TEXT NOT NULL);
        ''')
        self.db.commit()
    def close(self):
        self.db.close()
    def get(self, day):
        r = self.db.execute('SELECT * FROM days WHERE day=?', (day,)).fetchone()
        return dict(r) if r else None
    def put(self, day, **fields):
        allowed = {'status', 'error', 'pdf', 'url', 'expires', 'notified'}
        if not set(fields) <= allowed:
            raise ValueError('无效状态字段')
        self.db.execute('INSERT OR IGNORE INTO days(day,status,updated) VALUES(?,?,?)', (day, 'pending', now().isoformat()))
        fields['updated'] = now().isoformat()
        self.db.execute('UPDATE days SET ' + ','.join(k+'=?' for k in fields) + ' WHERE day=?', (*fields.values(), day))
        self.db.commit()
    def all(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM days ORDER BY day DESC')]
