import gzip
import time
import unittest
from copy import deepcopy
from unittest.mock import patch

from monitor.demo import fixture
from monitor.github import GitHub, GitHubRateLimitError, SnapshotCache
from monitor.model import iso, snapshot, state
from monitor.server import collect_snapshot, safe_snapshot, stale_after_seconds

NOW = 1800000000
CAPS = {'build': 8, 'general': 174, 'light': 20}


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.raw = fixture(NOW)
        self.result = snapshot(self.raw, NOW, CAPS)
        self.runs = {r['id']: r for r in self.result['runs']}

    def test_queued_timestamp_is_not_actual_start(self):
        run = self.runs[104]
        self.assertIsNone(run['actual_start'])
        self.assertEqual(run['next_start']['kind'], 'window')
        self.assertEqual(state({'status': 'queued', 'started_at': iso(NOW)}), 'waiting')

    def test_develop_push_contributes_to_queue_rank(self):
        self.assertTrue(self.runs[101]['develop_push'])
        self.assertFalse(self.runs[104]['develop_push'])
        self.raw['runs'] = [run for run in self.raw['runs'] if run['id'] != 101]
        without_push = snapshot(self.raw, NOW, CAPS)
        own_run = next(run for run in without_push['runs'] if run['id'] == 104)
        self.assertEqual(self.runs[104]['jobs'][0]['ahead'], own_run['jobs'][0]['ahead'] + 1)

    def test_mixed_progress_and_stalled_pool(self):
        run = self.runs[100]
        self.assertEqual(run['stages']['medium']['done'], 1)
        self.assertEqual(run['stages']['shell']['waiting'], 1)
        shell = next(j for j in run['jobs'] if j['stage'] == 'shell')
        self.assertEqual(shell['estimate']['reason'], 'stalled')
        self.assertEqual(run['completion']['kind'], 'unknown')
        self.assertEqual(run['stages']['other']['skipped'], 1)

    def test_overrun_and_matching_run_scenario(self):
        build = self.runs[102]['jobs'][0]
        self.assertEqual(build['estimate']['reason'], 'overrun')
        self.assertEqual(self.runs[103]['completion']['kind'], 'window')
        self.assertGreaterEqual(self.runs[103]['completion']['samples'], 3)
        self.assertIn('조건부', self.runs[103]['completion']['assumption'])

    def test_ci_completion_respects_known_queued_work(self):
        template = next(r for r in self.raw['runs'] if r['id'] == 104)
        for n in range(5):
            run = deepcopy(template)
            run['id'] = 300+n
            run['jobs'][0]['id'] = 3000+n
            run['jobs'][0]['created_at'] = iso(NOW-200+n)
            self.raw['runs'].append(run)
        result = snapshot(self.raw, NOW, dict(CAPS, light=1))
        run = next(r for r in result['runs'] if r['id'] == 103)
        known, whole = run['observed_finish'], run['completion']
        self.assertEqual(known['kind'], 'window')
        self.assertEqual(whole['kind'], 'window')
        self.assertGreaterEqual(whole['earliest'], known['earliest'])
        self.assertGreaterEqual(whole['latest'], known['latest'])

    def test_missing_history_and_incomplete_coverage(self):
        self.raw['runs'] = [r for r in self.raw['runs'] if r['status'] != 'completed']
        result = snapshot(self.raw, NOW, CAPS)
        run = next(r for r in result['runs'] if r['id'] == 104)
        self.assertEqual(run['next_start']['kind'], 'unknown')
        result = snapshot(fixture(NOW), NOW, CAPS, coverage=False)
        run = next(r for r in result['runs'] if r['id'] == 103)
        self.assertEqual(run['completion']['kind'], 'unknown')
        self.assertEqual(next(j for j in run['jobs'] if j['state'] == 'waiting')['estimate']['reason'], 'coverage')

    def test_stale_failed_and_partial_snapshots_hide_windows(self):
        for value, error, now in ((self.result, None, NOW+181), (self.result, 'offline', NOW)):
            safe = safe_snapshot(value, error, now)
            run = next(r for r in safe['runs'] if r['id'] == 103)
            self.assertEqual(run['completion']['kind'], 'unknown')
            self.assertIsNotNone(run['actual_start'])
        partial = dict(self.result, coverage=False)
        safe = safe_snapshot(partial, None, NOW)
        self.assertEqual(next(r for r in safe['runs'] if r['id'] == 104)['next_start']['reason'], 'coverage')
        self.assertEqual(self.runs[103]['completion']['kind'], 'window')

    def test_gate_only_is_excluded(self):
        self.raw['runs'].append({'id': 900, 'run_attempt': 1, 'status':'completed', 'jobs': [
            {'name':'gate', 'status':'completed', 'conclusion':'success', 'labels':['ubuntu-24.04']}]})
        self.assertNotIn(900, [r['id'] for r in snapshot(self.raw, NOW, CAPS)['runs']])

    def test_skipped_empty_label_job_does_not_block_pool_estimates(self):
        run = next(r for r in self.raw['runs'] if r['id'] == 103)
        run['jobs'].append({
            'id': 9999,
            'name': 'sql_by_cci / shard ${{ matrix.suite }} ${{ matrix.i }}',
            'status': 'completed',
            'conclusion': 'skipped',
            'labels': [],
            'created_at': iso(NOW - 30),
        })
        result = snapshot(self.raw, NOW, CAPS)
        pools = {p['name']: p for p in result['pools']}
        self.assertEqual(pools['light']['condition']['kind'], 'model')
        self.assertNotEqual(pools['build']['condition'].get('reason'), 'unknown_jobs')
        updated_103 = next(r for r in result['runs'] if r['id'] == 103)
        self.assertEqual(updated_103['completion']['kind'], 'window')

    def test_pool_breakdown_and_entries(self):
        self.raw['runs'].append({
            'id': 105,
            'run_attempt': 1,
            'status': 'in_progress',
            'display_title': 'PR #8605 · 세부 풀 분류',
            'pr': 8605,
            'author': 'tester',
            'created_at': iso(NOW - 20),
            'jobs': [
                {'id': 1051, 'name': 'build (debug)', 'labels': ['cubrid-arc-build'], 'status': 'queued', 'created_at': iso(NOW - 10)},
                {'id': 1052, 'name': 'sql / shard sql 01', 'labels': ['cubrid-arc'], 'status': 'in_progress', 'created_at': iso(NOW - 15), 'started_at': iso(NOW - 10)},
                {'id': 1053, 'name': 'sql_by_cci / shard sql_by_cci 00', 'labels': ['cubrid-arc'], 'status': 'queued', 'created_at': iso(NOW - 5)},
                {'id': 1054, 'name': 'shell / collect', 'labels': ['cubrid-arc-light'], 'status': 'in_progress', 'created_at': iso(NOW - 15), 'started_at': iso(NOW - 10)},
                {'id': 1055, 'name': 'shell / shard shell 02', 'labels': ['cubrid-arc-light'], 'status': 'queued', 'created_at': iso(NOW - 5)},
                {'id': 1056, 'name': 'plan', 'labels': ['cubrid-arc-light'], 'status': 'queued', 'created_at': iso(NOW - 5)},
            ],
        })
        result = snapshot(self.raw, NOW, CAPS)
        pools = {p['name']: p for p in result['pools']}

        self.assertEqual(pools['build']['label'], 'cubrid-arc-build')
        build_cats = {c['key']: c for c in pools['build']['breakdown']}
        self.assertEqual(build_cats['release']['running'], 1)
        self.assertEqual(build_cats['release']['waiting'], 1)
        self.assertEqual(build_cats['debug']['waiting'], 1)

        self.assertEqual(pools['general']['label'], 'cubrid-arc')
        gen_cats = {c['key']: c for c in pools['general']['breakdown']}
        self.assertEqual(gen_cats['shell']['waiting'], 1)
        self.assertEqual(gen_cats['sql']['running'], 1)
        self.assertEqual(gen_cats['sql_by_cci']['waiting'], 1)

        self.assertEqual(pools['light']['label'], 'cubrid-arc-light')
        light_cats = {c['key']: c for c in pools['light']['breakdown']}
        self.assertEqual(light_cats['plan']['waiting'], 1)
        self.assertEqual(light_cats['medium']['waiting'], 3)
        self.assertEqual(light_cats['collect']['running'], 1)
        self.assertEqual(light_cats['rerun']['waiting'], 1)

        entry_105 = next(e for e in pools['light']['entries'] if e['id'] == 105)
        self.assertEqual(entry_105['running'], 1)
        self.assertEqual(entry_105['waiting'], 2)
        self.assertEqual([c['key'] for c in entry_105['categories']], ['plan', 'collect', 'rerun'])


class AcquisitionTests(unittest.TestCase):
    def test_attempt_and_all_job_pages(self):
        calls = []
        def api(path):
            calls.append(path)
            if '/jobs?' in path:
                return {'jobs': [{'id': i} for i in range(100)] if '&page=1' in path else [{'id': 100}]}
            return {'run_attempt': 2}
        gh = GitHub(api)
        run = gh.attempt({'id': 42, 'run_attempt': 2, 'status': 'in_progress'})
        self.assertEqual(len(run['jobs']), 101)
        self.assertTrue(any('/attempts/2/jobs' in call and 'page=2' in call for call in calls))
        with self.assertRaisesRegex(RuntimeError, 'attempt changed'):
            gh.attempt({'id': 42, 'run_attempt': 1, 'status': 'in_progress'})

    def test_active_pagination_reports_truncation(self):
        def api(path):
            if path == 'user': return {'login': 'me'}
            if '/pulls/' in path: return {'user': {'login': 'author'}}
            if '/attempts/' in path: return {'jobs': []}
            if '/actions/runs/' in path: return {'run_attempt': 1}
            if 'status=queued' in path:
                return {'workflow_runs': [{'id': 1, 'run_attempt': 1, 'status': 'queued'}] * 100}
            return {'workflow_runs': []}
        data = GitHub(api).collect()
        self.assertFalse(data['coverage'])
        self.assertIn('300', data['warnings'][0])

    def test_collect_skips_skipped_completed_runs_and_caches_failed_completed(self):
        calls = []
        def api(path):
            calls.append(path)
            if path == 'user':
                return {'login': 'vimkim'}
            if 'status=completed' in path:
                return {'workflow_runs': [
                    {'id': 1, 'run_attempt': 1, 'status': 'completed', 'conclusion': 'skipped', 'display_title': 'PR #111'},
                    {'id': 2, 'run_attempt': 1, 'status': 'completed', 'conclusion': 'success', 'updated_at': 'u2', 'display_title': 'PR #222'},
                    {'id': 3, 'run_attempt': 2, 'status': 'completed', 'conclusion': 'failure', 'updated_at': 'u3', 'display_title': 'PR #333'},
                ]}
            if '/workflows/' in path:
                return {'workflow_runs': []}
            if '/runs/2/attempts/1/jobs' in path:
                return {'jobs': [{'id': 20, 'name': 'build (release)', 'status': 'completed', 'conclusion': 'success', 'labels': ['cubrid-arc-build']}]}
            if '/runs/3/attempts/2/jobs' in path:
                raise RuntimeError('gh: Server Error (HTTP 502)')
            if '/pulls/222' in path:
                return {'user': {'login': 'alice'}}
            if '/pulls/333' in path:
                return {'user': {'login': 'bob'}}
            raise AssertionError(f'unexpected call: {path}')

        gh = GitHub(api)
        first = gh.collect()
        self.assertEqual([r['id'] for r in sorted(first['runs'], key=lambda x: x['id'])], [2, 3])
        self.assertFalse(any('/pulls/111' in c or '/runs/1/' in c for c in calls))
        # Completed runs skip redundant GET /actions/runs/{id}
        self.assertFalse(any(c.endswith('/actions/runs/2') or c.endswith('/actions/runs/3') for c in calls))

        calls.clear()
        second = gh.collect()
        self.assertEqual(len(second['runs']), 2)
        # user is cached, run 2 is cached, and run 3 (502) is in cooldown so neither /jobs nor /user is called!
        self.assertFalse(any('/jobs' in c or c == 'user' or '/pulls/' in c for c in calls))
        self.assertTrue(any('재시도 대기' in w for w in second['warnings']))

    def test_model_clock_is_read_after_collection(self):
        clock = {'now': NOW}
        class SlowGitHub:
            def collect(self):
                clock['now'] += 90
                return fixture(NOW)
        with patch('monitor.server.time.time', side_effect=lambda: clock['now']):
            result = collect_snapshot(SlowGitHub(), False, CAPS)
        self.assertEqual(result['observed_at'], iso(NOW+90))

    def test_failed_refresh_keeps_last_good_value(self):
        calls = iter([{'observed_at': 'old'}, RuntimeError('offline')])
        def loader():
            x = next(calls)
            if isinstance(x, Exception): raise x
            return x
        cache = SnapshotCache(loader, interval=0)
        cache.refresh()
        cache.refresh()
        self.assertEqual(cache.value, {'observed_at':'old'})
        self.assertEqual(cache.error, 'offline')

    def test_snapshot_cache_background_polling_and_retry_backoff(self):
        results = iter([RuntimeError('boom'), {'runs': [], 'observed_at': 'ok'}])
        def loader():
            item = next(results, {'runs': [], 'observed_at': 'ok'})
            if isinstance(item, Exception):
                raise item
            return item
        cache = SnapshotCache(loader, interval=0.05, retry_intervals=(0.02,), active_interval=None)
        cache.start()
        try:
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                value, error, _ = cache.get()
                if value == {'runs': [], 'observed_at': 'ok'} and error is None:
                    break
                time.sleep(0.01)
            value, error, _ = cache.get()
            self.assertEqual(value, {'runs': [], 'observed_at': 'ok'})
            self.assertIsNone(error)
            self.assertIsNotNone(cache.meta()['duration_ms'])
        finally:
            cache.stop()

    def test_collect_auto_startup_fast_pass_runs_only_once(self):
        status_calls = []
        def api(path):
            if '/workflows/' in path:
                status_calls.append(path)
                return {'workflow_runs': []}
            if path == 'user':
                return {'login': 'vimkim'}
            raise AssertionError(f'unexpected call: {path}')

        previous = {'runs': [{'id': 1, 'run_attempt': 1, 'status': 'completed', 'jobs_complete': True, 'jobs': []}]}
        gh = GitHub(api, previous=previous)

        # 1st call: fast active-only pass (no status=completed) + schedules 1-shot followup
        gh.collect_auto()
        self.assertTrue(gh.needs_history_followup)
        self.assertFalse(any('status=completed' in c for c in status_calls))

        # 2nd call: followup fetches completed history and clears followup flag
        status_calls.clear()
        gh.collect_auto()
        self.assertFalse(gh.needs_history_followup)
        self.assertTrue(any('status=completed' in c for c in status_calls))

        # 3rd call: stays on fast active-only pass without re-triggering followup loop
        status_calls.clear()
        gh.collect_auto()
        self.assertFalse(gh.needs_history_followup)
        self.assertFalse(any('status=completed' in c for c in status_calls))

    def test_rate_limit_short_circuits_and_waits_until_reset(self):
        reset_at = time.time() + 120
        class FakeResp:
            def __init__(self, status, body, headers):
                self.status = status
                self._body = body
                self._headers = {k.lower(): v for k, v in headers.items()}
            def read(self):
                return self._body
            def getheader(self, name):
                return self._headers.get(name.lower())

        class FakeConn:
            requests = 0
            def request(self, method, path, headers=None):
                FakeConn.requests += 1
            def getresponse(self):
                return FakeResp(403, b'{"message":"API rate limit exceeded"}', {
                    'X-RateLimit-Remaining': '0',
                    'X-RateLimit-Reset': str(int(reset_at - 2)),
                })
            def close(self):
                pass

        gh = GitHub()
        with patch.object(gh, '_get_token', return_value='tok'), \
             patch('monitor.github.http.client.HTTPSConnection', return_value=FakeConn()):
            with self.assertRaises(GitHubRateLimitError) as ctx:
                gh._http_get('user')
            self.assertIn('한도 초과', str(ctx.exception))
            self.assertEqual(FakeConn.requests, 1)
            # Second call short-circuits before making any HTTP request
            with self.assertRaises(GitHubRateLimitError):
                gh._http_get('user')
            self.assertEqual(FakeConn.requests, 1)

        cache = SnapshotCache(lambda: gh.collect(), interval=60,
                              rate_limit_getter=lambda: (gh.rate_limit_remaining, gh.rate_limit_reset))
        cache.refresh()
        delay = cache._next_delay()
        self.assertGreater(delay, 100.0)
        self.assertEqual(cache.meta()['rate_limit_remaining'], 0)

    def test_http_etag_304_returns_cached_body(self):
        sent_headers = []
        responses = iter([
            (200, gzip.compress(b'{"login":"vimkim"}'), {'ETag': '"abc"', 'Content-Encoding': 'gzip', 'X-RateLimit-Remaining': '4999'}),
            (304, b'', {'ETag': '"abc"', 'X-RateLimit-Remaining': '4999'}),
        ])
        class FakeResp:
            def __init__(self, status, body, headers):
                self.status = status
                self._body = body
                self._headers = {k.lower(): v for k, v in headers.items()}
            def read(self):
                return self._body
            def getheader(self, name):
                return self._headers.get(name.lower())

        class FakeConn:
            def request(self, method, path, headers=None):
                sent_headers.append(dict(headers or {}))
            def getresponse(self):
                return FakeResp(*next(responses))
            def close(self):
                pass

        gh = GitHub()
        with patch.object(gh, '_get_token', return_value='tok'), \
             patch('monitor.github.http.client.HTTPSConnection', return_value=FakeConn()):
            first = gh._http_get('user')
            second = gh._http_get('user')
        self.assertEqual(first, {'login': 'vimkim'})
        self.assertEqual(second, {'login': 'vimkim'})
        self.assertNotIn('If-None-Match', sent_headers[0])
        self.assertEqual(sent_headers[1].get('If-None-Match'), '"abc"')

    def test_dynamic_stale_threshold_and_manual_trigger(self):
        self.assertEqual(stale_after_seconds(300), 660)
        self.assertEqual(stale_after_seconds(60), 180)
        raw = fixture(NOW)
        from monitor.model import snapshot
        snap = snapshot(raw, NOW, CAPS)
        not_stale = safe_snapshot(snap, None, NOW + 350, stale_after=660)
        self.assertEqual(next(r for r in not_stale['runs'] if r['id'] == 103)['completion']['kind'], 'window')
        stale = safe_snapshot(snap, None, NOW + 661, stale_after=660)
        self.assertEqual(next(r for r in stale['runs'] if r['id'] == 103)['completion']['reason'], 'stale')

        calls = 0
        def loader():
            nonlocal calls
            calls += 1
            return {'runs': [], 'observed_at': f'call-{calls}'}

        cache = SnapshotCache(loader, interval=300, active_interval=None, stale_after=660)
        cache.start()
        try:
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and calls < 1:
                time.sleep(0.01)
            self.assertEqual(calls, 1)
            # Inside cooldown, trigger returns False
            self.assertEqual(cache.trigger(min_cooldown=60), (False, False))
            # Bypass cooldown: trigger wakes the sleeping loop immediately
            self.assertEqual(cache.trigger(min_cooldown=0), (True, False))
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and calls < 2:
                time.sleep(0.01)
            self.assertEqual(calls, 2)
            self.assertEqual(cache.meta()['stale_after_seconds'], 660)
        finally:
            cache.stop()


if __name__ == '__main__': unittest.main()
