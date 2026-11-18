from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3


def cache_path():
    root = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache')
    return root / 'cubrid-gha-monitor' / 'observations.sqlite3'


class ObservationStore:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(sqlite3.connect(path)) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS observation (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                observed_at REAL NOT NULL,
                payload TEXT NOT NULL
            )''')

    def load(self):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT observed_at, payload FROM observation WHERE id = 1').fetchone()
        if row is None:
            return None
        observed_at, payload = row
        return observed_at, json.loads(payload)

    def save(self, observed_at, raw):
        payload = json.dumps(raw, ensure_ascii=False, separators=(',', ':'))
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('''INSERT INTO observation (id, observed_at, payload) VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET observed_at = excluded.observed_at, payload = excluded.payload
                WHERE excluded.observed_at >= observation.observed_at''', (observed_at, payload))
