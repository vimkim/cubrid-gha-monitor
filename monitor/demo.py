from .model import iso


def fixture(now):
    runs = []
    def job(id, name, label, status, created, start=None, end=None, conclusion=None):
        return {'id': id, 'name': name, 'labels': [label], 'status': status, 'conclusion': conclusion,
                'created_at': iso(now+created), 'started_at': iso(now+start) if start is not None else None,
                'completed_at': iso(now+end) if end is not None else None, 'runner_name': 'demo-runner' if status != 'queued' else ''}
    for n, duration in enumerate((240, 360, 480), 1):
        start = -86400*n
        runs.append({'id': n, 'run_attempt': 1, 'status': 'completed', 'display_title': f'PR #{8000+n} · 지난 실행',
                     'pr': 8000+n, 'author': 'colleague', 'created_at': iso(now+start-20), 'jobs': [
                         job(n*10, 'build (release)', 'cubrid-arc-build', 'completed', start-10, start, start+duration, 'success'),
                         job(n*10+1, 'shard shell 00', 'cubrid-arc', 'completed', start-10, start, start+duration, 'success'),
                         job(n*10+2, 'shard medium 00', 'cubrid-arc-light', 'completed', start-10, start, start+duration, 'success')]})
    for n, duration in enumerate((360, 480, 600), 1):
        start = -72000*n
        runs.append({'id': 200+n, 'run_attempt': 1, 'status': 'completed', 'conclusion': 'success',
                     'display_title': f'PR #{8100+n} · 완료된 동일 구성', 'author': 'colleague', 'created_at': iso(now+start-10), 'jobs': [
                         job(2000+n*10, 'build (debug)', 'cubrid-arc-build', 'completed', start-10, start, start+100, 'success'),
                         job(2001+n*10, 'plan', 'cubrid-arc-light', 'completed', start+100, start+100, start+160, 'success'),
                         job(2002+n*10, 'shard medium 00', 'cubrid-arc-light', 'completed', start+160, start+160, start+duration, 'success')]})
    runs.append({'id': 100, 'run_attempt': 2, 'status': 'queued', 'display_title': 'PR #8559 · OOS 체인 재사용', 'pr': 8559,
                 'author': 'vimkim', 'created_at': iso(now-900), 'jobs': [
                     job(1001, 'build (release)', 'cubrid-arc-build', 'completed', -900, -850, -500, 'success'),
                     job(1002, 'shard medium 00', 'cubrid-arc-light', 'completed', -450, -430, -100, 'success'),
                     job(1003, 'shard shell 00', 'cubrid-arc', 'queued', -800, -800),
                     job(1004, 'nightly batch', 'ubuntu-24.04', 'completed', -900, -900, -900, 'skipped')]})
    runs.append({'id': 101, 'run_attempt': 1, 'status': 'in_progress', 'display_title': '정렬 비용 개선 (#8586)',
                 'event': 'push', 'head_branch': 'develop', 'created_at': iso(now-1200), 'jobs': [
                     job(1011, 'build (release)', 'cubrid-arc-build', 'in_progress', -800, -700),
                     job(1012, 'shard medium 00', 'cubrid-arc-light', 'queued', -90, -90)]})
    runs.append({'id': 102, 'run_attempt': 1, 'status': 'queued', 'display_title': 'PR #8600 · 빌드 점검', 'pr': 8600,
                 'author': 'vimkim', 'created_at': iso(now-100), 'jobs': [job(1021, 'build (release)', 'cubrid-arc-build', 'queued', -60, -60)]})
    runs.append({'id': 103, 'run_attempt': 1, 'status': 'in_progress', 'display_title': 'PR #8601 · 테스트 계획 확인', 'pr': 8601,
                 'author': 'vimkim', 'created_at': iso(now-150), 'jobs': [
                     job(1031, 'build (debug)', 'cubrid-arc-build', 'completed', -150, -120, -80, 'success'),
                     job(1032, 'plan', 'cubrid-arc-light', 'completed', -80, -75, -35, 'success'),
                     job(1033, 'shard medium 00', 'cubrid-arc-light', 'queued', -30, -30)]})
    runs.append({'id': 104, 'run_attempt': 1, 'status': 'queued', 'display_title': 'PR #8602 · medium 시작 대기', 'pr': 8602,
                 'author': 'vimkim', 'created_at': iso(now-40), 'jobs': [job(1041, 'shard medium 00', 'cubrid-arc-light', 'queued', -30, -30)]})
    return {'source': 'demo' , 'viewer': 'vimkim', 'runs': runs, 'coverage': True, 'warnings': []}
