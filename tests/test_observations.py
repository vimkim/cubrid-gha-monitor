from collections import Counter
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit
from unittest import TestCase
from unittest.mock import patch

from monitor.demo import fixture
from monitor.github import AUTHOR_TTL, GitHub
from monitor.observations import ObservationStore
from monitor.server import collect_snapshot, render_snapshot, safe_snapshot

NOW = 1800000000
CAPS = {'build': 8, 'general': 174, 'light': 20}


class ActionsAPI:
    def __init__(self):
        self.runs = fixture(NOW)['runs']
        self.calls = Counter()
        self.failed_status = None

    def __call__(self, path):
        url = urlsplit(path)
        params = parse_qs(url.query)
        self.calls[url.path] += 1
        if path == 'user':
            return {'login': 'vimkim'}
        if '/pulls/' in path:
            return {'user': {'login': 'author'}}
        if '/workflows/' in path:
            status = params['status'][0]
            if status == self.failed_status:
                raise RuntimeError('GitHub is unavailable')
            return {'workflow_runs': [r for r in self.runs if r['status'] == status]}
        if '/attempts/' in path:
            _, number, _, attempt, _ = url.path.rsplit('/', 4)
            run = next(r for r in self.runs if r['id'] == int(number) and r['run_attempt'] == int(attempt))
            return {'jobs': run['jobs']}
        number = int(url.path.rsplit('/', 1)[1])
        return max((r for r in self.runs if r['id'] == number), key=lambda r: r['run_attempt'])


class ObservationTests(TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.path = Path(directory) / 'cache' / 'observations.sqlite3'
        self.store = ObservationStore(self.path)
        self.api = ActionsAPI()

    def collect(self, github, now=NOW):
        with patch('monitor.server.time.time', return_value=now):
            return collect_snapshot(github, False, CAPS, self.store)

    def test_restart_reuses_unchanged_completed_attempts_and_reconciles_remote(self):
        first = self.collect(GitHub(self.api))
        self.assertEqual(sum('/jobs' in path for path in self.api.calls), len(self.api.runs))
        self.assertEqual(first['coverage'], True)
        observed_at, raw = ObservationStore(self.path).load()
        restored = render_snapshot(raw, observed_at, CAPS)
        self.assertEqual(restored, first)
        self.assertEqual(render_snapshot(raw, observed_at, dict(CAPS, light=5))['pools'][2]['capacity'], 5)
        stale = safe_snapshot(restored, None, NOW + 181)
        stale_run = next(r for r in stale['runs'] if r['id'] == 104)
        self.assertEqual(stale['observed_at'], first['observed_at'])
        self.assertEqual(stale_run['next_start']['reason'], 'stale')

        changed = next(r for r in self.api.runs if r['id'] == 1)
        changed['updated_at'] = 'new revision'
        retitled = next(r for r in self.api.runs if r['id'] == 201)
        retitled['display_title'] = 'PR #8101 · 최신 GitHub 제목'
        deleted = next(r for r in self.api.runs if r['id'] == 3)
        self.api.runs.remove(deleted)
        rerun = deepcopy(next(r for r in self.api.runs if r['id'] == 2))
        rerun.update(run_attempt=2, status='in_progress')
        self.api.runs.append(rerun)
        new = deepcopy(next(r for r in self.api.runs if r['id'] == 104))
        new['id'] = 105
        self.api.runs.append(new)

        self.api.calls.clear()
        result = self.collect(GitHub(self.api, previous=raw), NOW + 10)
        requested_jobs = {path for path in self.api.calls if path.endswith('/jobs')}
        refreshed = {(1, 1), (2, 2), (100, 2), (101, 1), (102, 1), (103, 1), (104, 1), (105, 1)}
        self.assertEqual(requested_jobs, {f'repos/CUBRID/cubrid/actions/runs/{run}/attempts/{attempt}/jobs'
                                          for run, attempt in refreshed})
        self.assertFalse(any('/pulls/' in path for path in self.api.calls))
        self.assertNotIn(3, [r['id'] for r in result['runs']])
        self.assertEqual([r['attempt'] for r in result['runs'] if r['id'] == 2], [2])
        self.assertEqual(next(r for r in result['runs'] if r['id'] == 201)['title'], retitled['display_title'])
        self.assertEqual(self.store.load()[0], NOW + 10)

    def test_fast_active_refresh_reuses_cached_completed_runs(self):
        self.collect(GitHub(self.api))
        _, raw = self.store.load()
        gh = GitHub(self.api, previous=raw)
        self.api.calls.clear()
        fast = gh.collect(refresh_history=False)
        self.assertTrue(fast['history_available'])
        completed_ids = {r['id'] for r in fast['runs'] if r['status'] == 'completed'}
        self.assertEqual(completed_ids, {1, 2, 3, 201, 202, 203})
        # Active runs were still refreshed without listing completed runs.
        self.assertTrue(any('/attempts/' in path for path in self.api.calls))

    def test_incomplete_remote_list_preserves_stored_observation(self):
        github = GitHub(self.api)
        self.collect(github)
        saved = self.store.load()
        for status in ('completed', 'queued'):
            with self.subTest(status=status):
                self.api.failed_status = status
                value = self.collect(github, NOW + 60)
                self.assertTrue(value['warnings'])
                self.assertEqual(self.store.load(), saved)

    def test_expired_pr_authors_are_rechecked_after_restart(self):
        self.collect(GitHub(self.api))
        _, raw = self.store.load()
        self.api.calls.clear()
        self.collect(GitHub(self.api, previous=raw), NOW + AUTHOR_TTL)
        self.assertTrue(any('/pulls/' in path for path in self.api.calls))

    def test_older_observation_cannot_replace_newer_cache(self):
        self.collect(GitHub(self.api))
        saved = self.store.load()
        ObservationStore(self.path).save(NOW - 1, {'runs': []})
        self.assertEqual(self.store.load(), saved)
