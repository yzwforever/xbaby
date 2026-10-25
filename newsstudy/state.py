import hashlib
import json
import sqlite3
from pathlib import Path
from .settings import now, atomic_json

class State:
    def __init__(self, root):
        self.root = Path(root).resolve()
        p = self.root / 'data/state.sqlite3'
        fresh = not p.exists()
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
          CREATE TABLE IF NOT EXISTS recipients (
            day TEXT NOT NULL, recipient TEXT NOT NULL, status TEXT NOT NULL,
            PRIMARY KEY(day, recipient));
        ''')
        self.db.commit()
        if (fresh or not self.db.execute('SELECT 1 FROM days LIMIT 1').fetchone()) and (self.root / 'data/delivery-state.json').is_file():
            # A fresh checkout/GUI must honor the same durable delivery holds.
            try:
                self.restore_public()
            except Exception:
                self.db.close()
                raise
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

    @staticmethod
    def recipient_id(key):
        return hashlib.sha256(key.encode('utf-8')).hexdigest()

    def recipient_status(self, day, recipient):
        row = self.db.execute('SELECT status FROM recipients WHERE day=? AND recipient=?',
                              (day, recipient)).fetchone()
        return row['status'] if row else None

    def mark_recipient(self, day, recipient, status):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO recipients VALUES(?,?,?)', (day, recipient, status))
            self.db.execute('UPDATE days SET notified=2 WHERE day=?', (day,))

    def export_public(self):
        # Explicit whitelist: never serialize SQLite bytes, errors, paths, URLs or keys.
        value = {'version': 1, 'days': [
            {k: r[k] for k in ('day', 'status', 'notified')} for r in self.all()],
            'recipients': [dict(r) for r in self.db.execute(
                'SELECT day,recipient,status FROM recipients ORDER BY day,recipient')]}
        self.validate_public(value)
        atomic_json(self.root / 'data/delivery-state.json', value)

    @staticmethod
    def validate_public(value):
        import re
        from datetime import date
        if set(value) != {'version', 'days', 'recipients'} or value['version'] != 1:
            raise ValueError('投递状态快照格式无效')
        days = set()
        for row in value['days']:
            if (set(row) != {'day', 'status', 'notified'} or
                date.fromisoformat(row['day']).isoformat() != row['day'] or row['day'] in days or
                row['status'] not in ('pending', 'rendered', 'uploaded') or row['notified'] not in (0, 1, 2)):
                raise ValueError('投递日期状态无效')
            days.add(row['day'])
        seen = set()
        for row in value['recipients']:
            pair = (row.get('day'), row.get('recipient'))
            if (set(row) != {'day', 'recipient', 'status'} or row['day'] not in days or
                not re.fullmatch('[0-9a-f]{64}', row['recipient']) or pair in seen or
                row['status'] not in ('uncertain', 'sent', 'retry-approved')):
                raise ValueError('接收人状态无效')
            seen.add(pair)

    def restore_public(self):
        p = self.root / 'data/delivery-state.json'
        if not p.exists():
            # Old CI runs may have sent messages whose final push was rejected.
            # Missing evidence is NOT permission to send those dates again.
            with self.db:
                self.db.execute("UPDATE days SET notified=2 WHERE notified=0 AND status IN ('rendered','uploaded')")
                for pdf in (self.root / 'output/pdf').glob('????-??-??/report.pdf'):
                    self.db.execute('INSERT OR IGNORE INTO days(day,status,pdf,notified,updated) VALUES(?,?,?,?,?)',
                        (pdf.parent.name, 'rendered', str(pdf), 2, now().isoformat()))
            return
        value = json.loads(p.read_text(encoding='utf-8'))
        self.validate_public(value)
        with self.db:
            self.db.execute('DELETE FROM recipients')
            self.db.execute('DELETE FROM outbox')
            self.db.execute('DELETE FROM days')
            for row in value['days']:
                self.db.execute('INSERT INTO days(day,status,pdf,notified,updated) VALUES(?,?,?,?,?)',
                    (row['day'], row['status'], str(self.root / 'output/pdf' / row['day'] / 'report.pdf'),
                     row['notified'], now().isoformat()))
            self.db.executemany('INSERT INTO recipients VALUES(:day,:recipient,:status)', value['recipients'])
