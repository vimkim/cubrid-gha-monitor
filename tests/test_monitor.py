import unittest
from copy import deepcopy
from unittest.mock import patch

from monitor.demo import fixture
from monitor.github import GitHub, SnapshotCache
from monitor.model import iso, snapshot, state
from monitor.server import collect_snapshot, safe_snapshot

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


if __name__ == '__main__': unittest.main()
