from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import http.client
import json
import re
import subprocess
import threading
import time

BASE = 'repos/CUBRID/cubrid'
WORKFLOW = f'{BASE}/actions/workflows/gha-ci.yml/runs'
ACTIVE = ('queued', 'in_progress', 'waiting', 'requested', 'pending')
RUN_FIELDS = ('id', 'run_attempt', 'status', 'conclusion', 'display_title', 'created_at',
              'updated_at', 'event', 'head_branch', 'pull_requests')
JOB_FIELDS = ('id', 'name', 'status', 'conclusion', 'created_at', 'started_at',
              'completed_at', 'runner_name', 'labels')
AUTHOR_TTL = 86400
VIEWER_TTL = 3600
TOKEN_TTL = 1800
FAILED_COMPLETED_TTL = 1800
HISTORY_REFRESH_INTERVAL = 180
MAX_WORKERS = 10


class GitHubAPIError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def reusable_attempt(run):
    return (run['status'] == 'completed' and run.get('jobs_complete', False)
            and all(job.get('status') == 'completed' for job in run['jobs']))


def _iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')


class GitHub:
    def __init__(self, api=None, previous=None):
        self._use_http = api is None
        self.api = self._http_get if api is None else api
        previous_runs = previous['runs'] if previous is not None else []
        self.completed = {(r['id'], r['run_attempt']): (r.get('updated_at'), r['jobs'])
                          for r in previous_runs if reusable_attempt(r)}
        self.authors = {r['pr']: (r['author'], r['author_checked_at']) for r in previous_runs
                        if r.get('pr') and 'author_checked_at' in r}
        self.last_completed_runs = [r for r in previous_runs if r.get('status') == 'completed']
        self.last_history_at = None
        self.last_active_ids = {r['id'] for r in previous_runs if r.get('status') != 'completed'}
        self.failed_completed = {}
        self.viewer_cache = None
        self.needs_history_followup = False
        self.lock = threading.Lock()
        self._token_lock = threading.Lock()
        self._token = None
        self._token_at = None
        self._local = threading.local()

    @staticmethod
    def gh(path):
        result = subprocess.run(['gh', 'api', path], text=True, capture_output=True, timeout=35)
        if result.returncode:
            raise RuntimeError(result.stderr.strip()[:300] or 'gh api failed')
        return json.loads(result.stdout)

    def _get_token(self, force=False):
        with self._token_lock:
            now = time.monotonic()
            if not force and self._token and self._token_at is not None and now - self._token_at < TOKEN_TTL:
                return self._token
            try:
                result = subprocess.run(['gh', 'auth', 'token'], text=True, capture_output=True, timeout=10)
            except (OSError, subprocess.SubprocessError):
                return None
            token = result.stdout.strip()
            if result.returncode or not token:
                return None
            self._token = token
            self._token_at = time.monotonic()
            return token

    def _close_conn(self):
        conn = getattr(self._local, 'conn', None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    def _http_get(self, path):
        token = self._get_token()
        if not token:
            return self.gh(path)
        req_path = path if path.startswith('/') else f'/{path}'
        refreshed = False
        for attempt in range(3):
            conn = getattr(self._local, 'conn', None)
            if conn is None:
                conn = http.client.HTTPSConnection('api.github.com', timeout=25)
                self._local.conn = conn
            headers = {
                'Authorization': f'Bearer {token}',
                'Accept': 'application/vnd.github+json',
                'Accept-Encoding': 'gzip',
                'X-GitHub-Api-Version': '2022-11-28',
                'User-Agent': 'cubrid-gha-monitor',
                'Connection': 'keep-alive',
            }
            try:
                conn.request('GET', req_path, headers=headers)
                resp = conn.getresponse()
                body = resp.read()
                status = resp.status
                encoding = (resp.getheader('Content-Encoding') or '').lower()
                conn_hdr = (resp.getheader('Connection') or '').lower()
            except (http.client.HTTPException, OSError, TimeoutError) as exc:
                self._close_conn()
                if attempt < 2:
                    continue
                raise GitHubAPIError(f'gh: {exc}') from exc
            if conn_hdr == 'close':
                self._close_conn()
            if encoding == 'gzip':
                body = gzip.decompress(body)
            if status == 401 and not refreshed:
                refreshed = True
                new_token = self._get_token(force=True)
                if new_token:
                    token = new_token
                    continue
            if status >= 400:
                msg = ''
                try:
                    payload = json.loads(body.decode('utf-8', 'replace'))
                    msg = payload.get('message') or ''
                except ValueError:
                    msg = body.decode('utf-8', 'replace').strip()
                raise GitHubAPIError(f'gh: HTTP {status}: {msg[:240]}', status=status)
            return json.loads(body.decode('utf-8'))
        raise GitHubAPIError('gh: request retries exhausted')

    def pages(self, path, key, limit):
        items = []
        for page in range(1, limit + 1):
            sep = '&' if '?' in path else '?'
            result = self.api(f'{path}{sep}per_page=100&page={page}')
            chunk = result[key]
            items.extend(chunk)
            if len(chunk) < 100:
                return items, True
        return items, False

    def completed_pages(self, path, max_runs=100, limit=2):
        kept = []
        for page in range(1, limit + 1):
            sep = '&' if '?' in path else '?'
            result = self.api(f'{path}{sep}per_page=100&page={page}')
            chunk = result['workflow_runs']
            for run in chunk:
                if run.get('conclusion') == 'skipped':
                    continue
                kept.append(run)
                if len(kept) >= max_runs:
                    return kept[:max_runs], True
            if len(chunk) < 100:
                return kept, True
        return kept, False

    def _viewer(self):
        with self.lock:
            cached = self.viewer_cache
        if cached is not None and 0 <= time.monotonic() - cached[1] < VIEWER_TTL:
            return cached[0]
        value = self.api('user').get('login')
        with self.lock:
            self.viewer_cache = (value, time.monotonic())
        return value

    def attempt(self, run):
        key = (run['id'], run['run_attempt'])
        updated_at = run.get('updated_at')
        is_completed = run['status'] == 'completed'
        with self.lock:
            cached = self.completed.get(key)
            failed = self.failed_completed.get(key) if is_completed else None
        if is_completed and cached is not None and cached[0] == updated_at:
            return dict(run, jobs=cached[1], jobs_complete=True)
        if failed is not None and failed[0] == updated_at and 0 <= time.monotonic() - failed[1] < FAILED_COMPLETED_TTL:
            raise RuntimeError(f'{failed[2]} (재시도 대기)')
        try:
            jobs, complete = self.pages(f'{BASE}/actions/runs/{key[0]}/attempts/{key[1]}/jobs', 'jobs', 10)
            if not is_completed:
                # A rerun can begin between listing runs and fetching the attempt.
                latest = self.api(f'{BASE}/actions/runs/{key[0]}')
                if latest['run_attempt'] != key[1]:
                    raise RuntimeError(f'run {key[0]} attempt changed during scan')
        except Exception as exc:
            if is_completed:
                with self.lock:
                    self.failed_completed[key] = (updated_at, time.monotonic(), str(exc))
            raise
        compact_jobs = [{field: job[field] for field in JOB_FIELDS if field in job} for job in jobs]
        result = dict(run, jobs=compact_jobs, jobs_complete=complete)
        if reusable_attempt(result):
            with self.lock:
                self.completed[key] = (updated_at, compact_jobs)
                self.failed_completed.pop(key, None)
        return result

    def author(self, number):
        with self.lock:
            cached = self.authors.get(number)
        if cached is not None and 0 <= time.time() - cached[1] < AUTHOR_TTL:
            return cached[0]
        value = (self.api(f'{BASE}/pulls/{number}').get('user') or {}).get('login')
        with self.lock:
            self.authors[number] = (value, time.time())
        return value

    def should_refresh_history(self, now_mono=None):
        now_mono = time.monotonic() if now_mono is None else now_mono
        return self.last_history_at is None or now_mono - self.last_history_at >= HISTORY_REFRESH_INTERVAL

    def collect_auto(self):
        with self.lock:
            has_history = bool(self.last_completed_runs)
            last_history_at = self.last_history_at
            prev_active_ids = set(self.last_active_ids)
        if last_history_at is None:
            if has_history:
                raw = self.collect(refresh_history=False)
                self.needs_history_followup = True
                return raw
            self.needs_history_followup = False
            return self.collect(refresh_history=True)
        if self.should_refresh_history():
            self.needs_history_followup = False
            return self.collect(refresh_history=True)
        raw = self.collect(refresh_history=False)
        current_active_ids = {r['id'] for r in raw['runs'] if r.get('status') != 'completed'}
        if prev_active_ids - current_active_ids:
            # An active run finished or disappeared; refresh completed history now.
            self.needs_history_followup = False
            return self.collect(refresh_history=True)
        self.needs_history_followup = False
        return raw

    def _list_status(self, status):
        if status == 'completed':
            return self.completed_pages(f'{WORKFLOW}?status=completed')
        return self.pages(f'{WORKFLOW}?status={status}', 'workflow_runs', 3)

    def collect(self, refresh_history=True):
        warnings, runs, complete = [], {}, True
        statuses = (*ACTIVE, 'completed') if refresh_history else ACTIVE
        history_available = True if refresh_history else (bool(self.last_completed_runs) or self.last_history_at is not None)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            list_futures = {executor.submit(self._list_status, status): status for status in statuses}
            viewer_future = executor.submit(self._viewer)

            for future in as_completed(list_futures):
                status = list_futures[future]
                try:
                    found, full = future.result()
                    if not full and status != 'completed':
                        complete = False
                        warnings.append(f'{status}: 300개 제한으로 활성 실행이 누락될 수 있습니다')
                    for run in found:
                        existing = runs.get(run['id'])
                        if existing is None or run['run_attempt'] >= existing['run_attempt']:
                            runs[run['id']] = {field: run[field] for field in RUN_FIELDS if field in run}
                except Exception as exc:
                    if status == 'completed':
                        history_available = False
                    else:
                        complete = False
                    warnings.append(f'{status}: {exc}')

            try:
                viewer = viewer_future.result()
            except Exception as exc:
                viewer = None
                warnings.append(f'사용자 정보: {exc}')

            numbers = set()
            for run in runs.values():
                pulls = run.get('pull_requests') or []
                match = re.search(r'\bPR\s*#(\d+)', run.get('display_title') or '', re.I)
                run['pr'] = pulls[0]['number'] if pulls else int(match.group(1)) if match else None
                if run['pr']:
                    numbers.add(run['pr'])

            attempt_futures = {executor.submit(self.attempt, run): run for run in runs.values()}
            author_futures = {executor.submit(self.author, n): n for n in numbers}

            result = []
            for future in as_completed(attempt_futures):
                run = attempt_futures[future]
                try:
                    item = future.result()
                    if not item['jobs_complete']:
                        complete = False
                        warnings.append(f"실행 {run['id']}: 작업 페이지 제한")
                    result.append(item)
                except Exception as exc:
                    if run['status'] != 'completed':
                        complete = False
                    warnings.append(f"실행 {run['id']}: {exc}")
                    result.append(dict(run, jobs=[], jobs_complete=False))

            authors = {}
            for future in as_completed(author_futures):
                number = author_futures[future]
                try:
                    authors[number] = future.result()
                except Exception as exc:
                    warnings.append(f'PR #{number} 작성자: {exc}')

            for run in result:
                number = run['pr']
                run['author'] = authors.get(number)
                if number in authors:
                    with self.lock:
                        entry = self.authors.get(number)
                    if entry is not None:
                        run['author_checked_at'] = entry[1]

        if not refresh_history:
            with self.lock:
                cached_completed = [dict(r) for r in self.last_completed_runs if r['id'] not in runs]
            result.extend(cached_completed)
            for r in cached_completed:
                if r.get('pr'):
                    numbers.add(r['pr'])
        elif history_available:
            with self.lock:
                self.last_completed_runs = [dict(r) for r in result if r['status'] == 'completed']
                self.last_history_at = time.monotonic()

        with self.lock:
            self.last_active_ids = {r['id'] for r in result if r.get('status') != 'completed'}
            if refresh_history and complete and history_available:
                completed_keys = {(r['id'], r['run_attempt']) for r in result if r['status'] == 'completed'}
                self.completed = {key: value for key, value in self.completed.items() if key in completed_keys}
                self.failed_completed = {key: value for key, value in self.failed_completed.items() if key in completed_keys}
                self.authors = {number: value for number, value in self.authors.items() if number in numbers}

        if not result and warnings:
            raise RuntimeError('; '.join(warnings[:3]))
        return {'source': 'live', 'viewer': viewer, 'runs': result, 'warnings': warnings,
                'coverage': complete, 'history_available': history_available,
                'history_scope': '최근 완료 실행 최대 100개 (스킵 제외)'}


class SnapshotCache:
    def __init__(self, loader, interval=60, initial=None, retry_intervals=(10, 20, 40, 60),
                 active_interval=45, followup_checker=None):
        self.loader = loader
        self.interval = interval
        self.retry_intervals = tuple(retry_intervals)
        self.active_interval = active_interval
        self.followup_checker = followup_checker
        self.value, self.error, self.updated = initial, None, None
        self.last_duration = None
        self.next_refresh = None
        self.consecutive_errors = 0
        self.lock = threading.Lock()
        self.loading = False
        self._started = False
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        with self.lock:
            if self._started:
                return
            self._started = True
            self._stop.clear()
            self.loading = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self, timeout=2):
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def _next_delay(self):
        with self.lock:
            err = self.error
            errs = self.consecutive_errors
            value = self.value
            duration = self.last_duration or 0.0
        if err:
            idx = min(max(errs - 1, 0), len(self.retry_intervals) - 1)
            return float(self.retry_intervals[idx])
        if self.followup_checker and self.followup_checker():
            return 1.0
        has_active = any(r.get('status') != 'completed' for r in (value or {}).get('runs', []))
        target = self.active_interval if (has_active and self.active_interval is not None) else self.interval
        if target <= 0:
            return 0.0
        return max(10.0, target - duration)

    def _loop(self):
        while not self._stop.is_set():
            self.refresh()
            delay = self._next_delay()
            with self.lock:
                self.next_refresh = time.time() + delay
            if self._stop.wait(delay):
                break

    def get(self):
        with self.lock:
            if not self._started and not self.loading and (self.updated is None or time.monotonic() - self.updated >= self.interval):
                self.loading = True
                threading.Thread(target=self.refresh, daemon=True).start()
            return self.value, self.error, self.loading

    def meta(self):
        with self.lock:
            return {
                'duration_ms': int(self.last_duration * 1000) if self.last_duration is not None else None,
                'next_refresh_at': _iso(self.next_refresh) if self.next_refresh is not None else None,
                'consecutive_errors': self.consecutive_errors,
                'interval_seconds': self.interval,
            }

    def refresh(self):
        started = time.monotonic()
        with self.lock:
            self.loading = True
        try:
            value = self.loader()
            with self.lock:
                self.value, self.error = value, None
                self.consecutive_errors = 0
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
                self.consecutive_errors += 1
        finally:
            with self.lock:
                self.last_duration = time.monotonic() - started
                self.updated, self.loading = time.monotonic(), False
