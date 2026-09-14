"""Loopback-only demo appliance service. Stdlib only; never controls real IoT."""
import argparse
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3

DEVICES = (
    ('living_room_ac', 'Living room air conditioner', '客厅空调', 'on'),
    ('master_bedroom_ac', 'Main bedroom air conditioner', '主卧空调', 'off'),
    ('second_bedroom_ac', 'Second bedroom air conditioner', '次卧空调', 'on'),
    ('lights', 'Lights', '电灯', 'on'),
    ('living_room_tv', 'Living room TV', '客厅电视', 'on'),
    ('robot_vacuum', 'Robot vacuum', '扫地机器人', 'off'),
)


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, name_en TEXT, name_zh TEXT, state TEXT)')
            db.execute("CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at TEXT DEFAULT CURRENT_TIMESTAMP, action TEXT, details TEXT)")
            db.executemany('INSERT OR IGNORE INTO devices VALUES (?,?,?,?)', DEVICES)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def snapshot(db):
        return {'success': True, 'simulated': True,
                'devices': [dict(row) for row in db.execute('SELECT * FROM devices ORDER BY rowid')]}

    def status(self):
        with closing(self.connect()) as db:
            return self.snapshot(db)

    def set(self, ids, state):
        if state not in ('on', 'off') or not isinstance(ids, list) or not ids or len(ids) > 6:
            raise ValueError('Use 1–6 device IDs and state on or off.')
        if any(not isinstance(i, str) or i not in {d[0] for d in DEVICES} for i in ids):
            raise ValueError('Unknown device ID; query available devices first. Nothing changed.')
        ids = list(dict.fromkeys(ids))
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            changed = []
            for device_id in ids:
                old = db.execute('SELECT state FROM devices WHERE id=?', (device_id,)).fetchone()['state']
                if old != state:
                    db.execute('UPDATE devices SET state=? WHERE id=?', (state, device_id))
                    changed.append(device_id)
            details = {'requested_ids': ids, 'requested_state': state, 'changed_ids': changed}
            cursor = db.execute('INSERT INTO audit(action,details) VALUES (?,?)', ('set', json.dumps(details)))
            result = {**self.snapshot(db), **details, 'operation_id': cursor.lastrowid}
        return result

    def reset(self):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            db.executemany('UPDATE devices SET state=? WHERE id=?', [(d[3], d[0]) for d in DEVICES])
            db.execute('INSERT INTO audit(action,details) VALUES (?,?)', ('operator_reset', '{}'))
            return self.snapshot(db)


def make_server(store, port=8769):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(3)

        def log_message(self, *_):
            pass

        def reply(self, status, data):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def allowed(self):
            # No browser-origin requests or DNS rebinding; local clients only.
            return (not self.headers.get('Origin') and
                    self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}')

        def do_GET(self):
            if not self.allowed():
                return self.reply(403, {'success': False, 'simulated': True})
            if self.path != '/devices':
                return self.reply(404, {'success': False, 'simulated': True})
            try:
                self.reply(200, store.status())
            except sqlite3.Error:
                self.reply(503, {'success': False, 'simulated': True, 'error': 'State database unavailable.'})

        def do_POST(self):
            if not self.allowed() or self.headers.get('Content-Type') != 'application/json':
                return self.reply(403, {'success': False, 'simulated': True})
            if self.path != '/devices/set':
                return self.reply(404, {'success': False, 'simulated': True})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    raise ValueError('Invalid request size.')
                args = json.loads(self.rfile.read(length))
                if not isinstance(args, dict):
                    raise ValueError('Expected a JSON object.')
                self.reply(200, store.set(args.get('device_ids'), args.get('state')))
            except (ValueError, TypeError) as exc:
                self.reply(400, {'success': False, 'simulated': True, 'error': str(exc)})
            except sqlite3.Error:
                self.reply(503, {'success': False, 'simulated': True, 'error': 'State database unavailable; query before retrying.'})

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['serve', 'status', 'reset-demo'])
    parser.add_argument('--db', required=True)
    parser.add_argument('--port', type=int, default=8769)
    args = parser.parse_args()
    store = Store(args.db)
    if args.action == 'serve':
        with make_server(store, args.port) as server:
            server.serve_forever()
    else:
        print(json.dumps(store.reset() if args.action == 'reset-demo' else store.status(), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
