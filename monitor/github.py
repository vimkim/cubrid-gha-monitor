from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
import subprocess
import threading
import time

BASE = 'repos/CUBRID/cubrid'
WORKFLOW = f'{BASE}/actions/workflows/gha-ci.yml/runs'
ACTIVE = ('queued', 'in_progress', 'waiting', 'requested', 'pending')


class GitHub:
    def __init__(self, api=None):
        self.api = api or self.gh
        self.completed = {}
        self.authors = {}
        self.lock = threading.Lock()

    @staticmethod
    def gh(path):
        result = subprocess.run(['gh', 'api', path], text=True, capture_output=True, timeout=35)
        if result.returncode:
            raise RuntimeError(result.stderr.strip()[:300] or 'gh api failed')
        return json.loads(result.stdout)

    def pages(self, path, key, limit):
        items = []
        for page in range(1, limit+1):
            sep = '&' if '?' in path else '?'
            result = self.api(f'{path}{sep}per_page=100&page={page}')
            chunk = result[key]
            items.extend(chunk)
            if len(chunk) < 100:
                return items, True
        return items, False

    def attempt(self, run):
        key = (run['id'], run['run_attempt'])
        if run['status'] == 'completed' and key in self.completed:
            return dict(self.completed[key])
        jobs, complete = self.pages(f'{BASE}/actions/runs/{key[0]}/attempts/{key[1]}/jobs', 'jobs', 10)
        # A rerun can begin between listing runs and fetching the attempt.
        latest = self.api(f'{BASE}/actions/runs/{key[0]}')
        if latest['run_attempt'] != key[1]:
            raise RuntimeError(f'run {key[0]} attempt changed during scan')
        result = dict(run, jobs=jobs, jobs_complete=complete)
        if run['status'] == 'completed' and complete:
            with self.lock:
                self.completed[key] = result
        return result

    def author(self, number):
        with self.lock:
            if number in self.authors:
                return self.authors[number]
        value = self.api(f'{BASE}/pulls/{number}').get('user', {}).get('login')
        with self.lock:
            self.authors[number] = value
        return value

    def collect(self):
        warnings, runs, complete = [], {}, True
        for status in (*ACTIVE, 'completed'):
            try:
                found, full = self.pages(f'{WORKFLOW}?status={status}', 'workflow_runs', 3 if status != 'completed' else 1)
                if not full and status != 'completed':
                    complete = False
                    warnings.append(f'{status}: 300개 제한으로 활성 실행이 누락될 수 있습니다')
                for run in found:
                    runs[(run['id'], run['run_attempt'])] = run
            except Exception as exc:
                if status != 'completed':
                    complete = False
                warnings.append(f'{status}: {exc}')
        try:
            viewer = self.api('user').get('login')
        except Exception as exc:
            viewer = None
            warnings.append(f'사용자 정보: {exc}')
        result = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(self.attempt, run): run for run in runs.values()}
            for future in as_completed(futures):
                run = futures[future]
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
            numbers = set()
            for run in result:
                pulls = run.get('pull_requests') or []
                match = re.search(r'\bPR\s*#(\d+)', run.get('display_title') or '', re.I)
                run['pr'] = pulls[0]['number'] if pulls else int(match.group(1)) if match else None
                if run['pr']:
                    numbers.add(run['pr'])
            futures = {executor.submit(self.author, n): n for n in numbers}
            authors = {}
            for future in as_completed(futures):
                try:
                    authors[futures[future]] = future.result()
                except Exception as exc:
                    warnings.append(f'PR #{futures[future]} 작성자: {exc}')
            for run in result:
                run['author'] = authors.get(run['pr'])
        if not result and warnings:
            raise RuntimeError('; '.join(warnings[:3]))
        return {'source': 'live', 'viewer': viewer, 'runs': result, 'warnings': warnings,
                'coverage': complete, 'history_scope': '최근 완료 실행 최대 100개'}


class SnapshotCache:
    def __init__(self, loader, interval=60):
        self.loader, self.interval = loader, interval
        self.value, self.error, self.updated = None, None, 0
        self.lock = threading.Lock()
        self.loading = False

    def get(self):
        with self.lock:
            if not self.loading and time.monotonic() - self.updated >= self.interval:
                self.loading = True
                threading.Thread(target=self.refresh, daemon=True).start()
            return self.value, self.error, self.loading

    def refresh(self):
        try:
            value = self.loader()
            with self.lock:
                self.value, self.error = value, None
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
        finally:
            with self.lock:
                self.updated, self.loading = time.monotonic(), False
