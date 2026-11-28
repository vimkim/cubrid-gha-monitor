import argparse
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import socket
import sqlite3
import time
from urllib.parse import urlsplit

from .demo import fixture
from .github import GitHub, SnapshotCache
from .model import snapshot, stamp, unknown
from .observations import ObservationStore, cache_path

STATIC = Path(__file__).resolve().parent.parent / 'static'


def stale_after_seconds(interval):
    return max(180, int(interval) * 2 + 60)


def safe_snapshot(value, error, now, stale_after=180):
    if not value:
        return value
    if error:
        reason = ('refresh_failed', '최근 갱신 실패')
    elif not value['coverage']:
        reason = ('coverage', '활성 작업 수집 불완전')
    elif now - (stamp(value['observed_at']) or 0) > stale_after:
        reason = ('stale', '관측 데이터가 오래됨')
    else:
        return value
    def suppress(item):
        if isinstance(item, dict):
            if item.get('kind') == 'window':
                return unknown(*reason)
            return {key: suppress(val) for key, val in item.items()}
        if isinstance(item, list):
            return [suppress(val) for val in item]
        return item
    return suppress(value)


def render_snapshot(raw, observed_at, capacities):
    result = snapshot(raw, observed_at, capacities, raw['coverage'])
    result['history_scope'] = raw.get('history_scope', '데모 이력')
    return result


def collect_snapshot(github, demo, capacities, store=None, auto=False):
    if demo:
        raw = fixture(time.time())
    elif auto and hasattr(github, 'collect_auto'):
        raw = github.collect_auto()
    else:
        raw = github.collect()
    observed_at = time.time()
    value = render_snapshot(raw, observed_at, capacities)
    if store is not None and raw['coverage'] and raw['history_available']:
        try:
            store.save(observed_at, raw)
        except sqlite3.Error as exc:
            logging.warning('Could not save GitHub observation: %s', exc)
    return value


def main():
    parser = argparse.ArgumentParser(description='Local CUBRID Actions dashboard')
    parser.add_argument('--host', default='127.0.0.1', help='address to bind (default: %(default)s)')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--interval', type=int, default=300, help='background refresh interval in seconds (default: %(default)s)')
    parser.add_argument('--build-capacity', type=int, default=8)
    parser.add_argument('--general-capacity', type=int, default=174)
    parser.add_argument('--light-capacity', type=int, default=20)
    args = parser.parse_args()
    if args.interval < 30 or args.interval > 3600:
        parser.error('interval must be between 30 and 3600 seconds')
    caps = {'build': args.build_capacity, 'general': args.general_capacity, 'light': args.light_capacity}
    if any(value < 0 or value > 10000 for value in caps.values()):
        parser.error('capacities must be between 0 and 10000')
    store, previous, initial = None, None, None
    if not args.demo:
        try:
            store = ObservationStore(cache_path())
            saved = store.load()
            if saved is not None:
                observed_at, raw = saved
                initial = render_snapshot(raw, observed_at, caps)
                previous = raw
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
            logging.warning('Could not restore GitHub observation: %s', exc)
    github = GitHub(previous=previous)
    stale_after = stale_after_seconds(args.interval)
    cache = SnapshotCache(
        lambda: collect_snapshot(github, args.demo, caps, store, auto=True),
        interval=args.interval,
        initial=initial,
        active_interval=None,
        stale_after=stale_after,
        followup_checker=(lambda: github.needs_history_followup) if not args.demo else None,
        rate_limit_getter=(lambda: (github.rate_limit_remaining, github.rate_limit_reset)) if not args.demo else None,
    )

    class Handler(BaseHTTPRequestHandler):
        def _allowed_host(self):
            host = self.headers.get('Host', '').split(':')[0]
            local_address = self.connection.getsockname()[0]
            return host in ('localhost', '127.0.0.1', args.host, local_address)

        def do_GET(self):
            if not self._allowed_host():
                self.send_error(403)
                return
            path = urlsplit(self.path).path
            if path == '/api/snapshot':
                value, error, loading = cache.get()
                payload = json.dumps({
                    'snapshot': safe_snapshot(value, error, time.time(), stale_after=stale_after),
                    'error': error,
                    'loading': loading,
                    'refresh': cache.meta(),
                }, ensure_ascii=False).encode()
                self.respond(payload, 'application/json; charset=utf-8')
            elif path in ('/', '/app.js', '/style.css'):
                name = 'index.html' if path == '/' else path[1:]
                mime = {'index.html': 'text/html', 'app.js': 'text/javascript', 'style.css': 'text/css'}[name]
                self.respond((STATIC / name).read_bytes(), mime + '; charset=utf-8')
            else:
                self.send_error(404)

        def do_POST(self):
            if not self._allowed_host():
                self.send_error(403)
                return
            if urlsplit(self.path).path == '/api/refresh':
                triggered, loading = cache.trigger()
                payload = json.dumps({'triggered': triggered, 'loading': loading}, ensure_ascii=False).encode()
                self.respond(payload, 'application/json; charset=utf-8')
            else:
                self.send_error(404)

        def respond(self, data, mime):
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; connect-src 'self'; style-src 'self'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

    with ThreadingHTTPServer((args.host, args.port), Handler) as server:
        notify_socket = os.environ.pop('NOTIFY_SOCKET', '')
        bound_host, bound_port = server.server_address
        host = bound_host if notify_socket else args.host
        url = f'http://{host}:{bound_port}'
        cache.start()
        print(f"{'DEMO' if args.demo else 'LIVE'} {url}", flush=True)
        if notify_socket:
            if notify_socket.startswith('@'):
                notify_socket = '\0' + notify_socket[1:]
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
                notifier.sendto(f'READY=1\nSTATUS={url}'.encode(), notify_socket)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            cache.stop()


if __name__ == '__main__':
    main()
