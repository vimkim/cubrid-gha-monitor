from collections import Counter, defaultdict
from datetime import datetime, timezone
import heapq
import re

POOLS = {'cubrid-arc-build': 'build', 'cubrid-arc': 'general', 'cubrid-arc-light': 'light'}
WAITING = {'queued', 'waiting', 'requested', 'pending'}


def stamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() if value else None
    except (ValueError, TypeError):
        return None


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')


def group(name):
    name = re.sub(r'^\w+ / ', '', name or '')
    return re.sub(r'(?<=\s)\d+$', '#', name)


def stage(name):
    name = name or ''
    m = re.search(r'(?:shard |^)(sql_by_cci|medium|shell|sql)(?:\s| /|$)', name)
    if m:
        return m.group(1)
    if name.startswith('build'):
        return 'build'
    if name == 'plan':
        return 'plan'
    return 'other'


def pool(job):
    return next((POOLS[x] for x in job.get('labels', []) if x in POOLS), 'hosted' if any(x.startswith('ubuntu-') for x in job.get('labels', [])) else 'unknown')


def state(job):
    status, conclusion = job.get('status'), job.get('conclusion')
    if status in WAITING:
        return 'waiting'
    if status == 'in_progress':
        return 'running'
    if status == 'completed':
        if conclusion == 'skipped':
            return 'skipped'
        if conclusion == 'cancelled':
            return 'cancelled'
        return 'done'
    return 'unknown'


def unknown(reason, text):
    return {'kind': 'unknown', 'reason': reason, 'text': text}


def window(start, end, samples, age):
    return {'kind': 'window', 'earliest': iso(start), 'latest': iso(end), 'samples': samples, 'sample_age_seconds': int(age)}


def snapshot(raw, now, capacities, coverage=True):
    history = defaultdict(list)
    for run in raw['runs']:
        if run.get('status') != 'completed' or not run.get('jobs_complete', True):
            continue
        for j in run['jobs']:
            start, end = stamp(j.get('started_at')), stamp(j.get('completed_at'))
            if state(j) == 'done' and j.get('conclusion') == 'success' and start and end and 0 < end - start < 86400:
                history[(pool(j), group(j.get('name')))].append((end-start, end, (run['id'], run['run_attempt'])))

    def sample(j, elapsed=0):
        values = history[(j['pool'], group(j['name']))]
        recent = [x for x in values if 0 <= now-x[1] <= 14*86400]
        if len({x[2] for x in recent}) < 3:
            return None, 'history'
        remaining = sorted(x[0]-elapsed for x in recent if x[0] > elapsed)
        if not remaining:
            return None, 'overrun'
        return (remaining[0], remaining[-1], len(recent), now-max(x[1] for x in recent)), None

    runs = []
    for run in raw['runs']:
        jobs = []
        for original in run['jobs']:
            j = {k: original.get(k) for k in ('id', 'name', 'status', 'conclusion', 'created_at', 'started_at', 'completed_at', 'runner_name')}
            j['state'], j['pool'], j['stage'] = state(original), pool(original), stage(original.get('name'))
            j['estimate'] = unknown('not_ready', '대기 중인 작업이 아닙니다')
            jobs.append(j)
        if not any(j['stage'] in ('build', 'plan', 'shell', 'sql', 'medium', 'sql_by_cci') and j['state'] not in ('skipped', 'cancelled') for j in jobs):
            if run.get('jobs_complete', True):
                continue
        pr = run.get('pr')
        if not pr:
            match = re.search(r'\bPR\s*#(\d+)', run.get('display_title') or '', re.I)
            pr = int(match.group(1)) if match else None
        actual = [stamp(j['started_at']) for j in jobs if j['stage'] in ('build', 'plan', 'shell', 'sql', 'medium', 'sql_by_cci') and j['state'] in ('running', 'done') and stamp(j['started_at'])]
        stages = {}
        for j in jobs:
            counts = stages.setdefault(j['stage'], defaultdict(int))
            counts[j['state']] += 1
        runs.append({'id': run['id'], 'attempt': run['run_attempt'], 'title': run.get('display_title') or '(제목 없음)', 'pr': pr,
                     'author': run.get('author'), 'status': run.get('status'), 'conclusion': run.get('conclusion'),
                     'develop_push': run.get('event') == 'push' and run.get('head_branch') == 'develop',
                     'created_at': run.get('created_at'), 'actual_start': iso(min(actual)) if actual else None,
                     'jobs_complete': run.get('jobs_complete', True), 'jobs': jobs, 'stages': stages,
                     'completion': unknown('future_work', '추가 단계 대기: 전체 완료 시각은 알 수 없음')})

    active = [r for r in runs if r['status'] != 'completed']
    pools = []
    for p in ('build', 'general', 'light'):
        all_jobs = [(r, j) for r in active for j in r['jobs'] if j['pool'] == p]
        waiting = sorted(((r, j) for r, j in all_jobs if j['state'] == 'waiting'), key=lambda x: (x[1]['created_at'] or '', x[1]['id'] or 0))
        running = [j for _, j in all_jobs if j['state'] == 'running']
        cap = capacities.get(p, 0)
        reason = None
        if not coverage or any(not r['jobs_complete'] for r in active):
            reason = ('coverage', '활성 작업 수집이 불완전합니다')
        elif any(j['state'] == 'unknown' or j['pool'] == 'unknown' for r in active for j in r['jobs']):
            reason = ('unknown_jobs', '알 수 없는 작업 상태 또는 풀')
        elif not cap or len(running) > cap:
            reason = ('capacity', '풀 용량 설정을 확인하세요')
        elif waiting and len(running) < cap and now - (stamp(waiting[0][1]['created_at']) or now) > 300:
            reason = ('stalled', '설정상 빈 자리에도 장시간 대기 중입니다')
        lower_slots, upper_slots = [], []
        if not reason:
            for j in running:
                started = stamp(j['started_at'])
                duration, why = sample(j, max(0, now-started) if started else 0)
                if not started or not duration:
                    reason = (why or 'timing', '실행 중 작업의 남은 시간을 알 수 없습니다')
                    break
                j['finish'] = window(now+duration[0], now+duration[1], duration[2], duration[3])
                heapq.heappush(lower_slots, duration[0])
                heapq.heappush(upper_slots, duration[1])
            lower_slots.extend([0] * max(0, cap - len(running)))
            upper_slots.extend([0] * max(0, cap - len(running)))
            heapq.heapify(lower_slots)
            heapq.heapify(upper_slots)
        for rank, (run, j) in enumerate(waiting):
            j['ahead'] = rank
            if reason:
                j['estimate'] = unknown(*reason)
                continue
            duration, why = sample(j)
            if not duration:
                j['estimate'] = unknown(why, '비교 가능한 최근 이력이 부족합니다' if why == 'history' else '이력 범위를 초과했습니다')
                reason = (why, '앞선 작업의 실행 시간을 알 수 없습니다')
                continue
            low, high = heapq.heappop(lower_slots), heapq.heappop(upper_slots)
            j['estimate'] = window(now+low, now+high, duration[2], duration[3])
            j['finish'] = window(now+low+duration[0], now+high+duration[1], duration[2], duration[3])
            heapq.heappush(lower_slots, low+duration[0])
            heapq.heappush(upper_slots, high+duration[1])
        pools.append({'name': p, 'capacity': cap, 'running': len(running), 'waiting': len(waiting), 'condition': unknown(*reason) if reason else {'kind': 'model', 'text': '생성 시각 순서 모델'}})
    def shape(run):
        return tuple(sorted(Counter((j['pool'], group(j['name'])) for j in run['jobs'] if j['state'] != 'skipped').items()))

    for run in active:
        waiting = [j for j in run['jobs'] if j['state'] == 'waiting']
        if run['actual_start']:
            run['next_start'] = {'kind': 'observed', 'at': run['actual_start']}
        elif waiting and all(j['estimate']['kind'] == 'window' for j in waiting):
            run['next_start'] = window(min(stamp(j['estimate']['earliest']) for j in waiting),
                                       min(stamp(j['estimate']['latest']) for j in waiting),
                                       min(j['estimate']['samples'] for j in waiting),
                                       max(j['estimate']['sample_age_seconds'] for j in waiting))
        else:
            run['next_start'] = unknown('start_unavailable', '관측된 다음 작업의 시작 시각을 알 수 없음')
        unfinished = [j for j in run['jobs'] if j['state'] in ('running', 'waiting', 'unknown')]
        if unfinished and all(j.get('finish', {}).get('kind') == 'window' for j in unfinished):
            run['observed_finish'] = window(max(stamp(j['finish']['earliest']) for j in unfinished),
                                            max(stamp(j['finish']['latest']) for j in unfinished),
                                            min(j['finish']['samples'] for j in unfinished),
                                            max(j['finish']['sample_age_seconds'] for j in unfinished))
        else:
            run['observed_finish'] = unknown('unfinished_jobs', '관측된 작업의 완료 시각을 알 수 없음')
        if not coverage or not run['jobs_complete'] or not run['actual_start'] or any(j['state'] in ('unknown', 'cancelled') for j in run['jobs']):
            continue
        if not any(j['stage'] == 'plan' and j['state'] == 'done' for j in run['jobs']) or not any(j['stage'] in ('shell', 'sql', 'medium', 'sql_by_cci') for j in run['jobs']):
            continue
        if any(j['state'] in ('running', 'waiting') and j.get('finish', {}).get('kind') != 'window' for j in run['jobs']):
            continue
        elapsed = now - stamp(run['actual_start'])
        matches = []
        for old in runs:
            if old['status'] != 'completed' or old['conclusion'] != 'success' or not old['jobs_complete'] or shape(old) != shape(run):
                continue
            end = max((stamp(j['completed_at']) or 0 for j in old['jobs']), default=0)
            start = stamp(old['actual_start'])
            if start and end > start and 0 <= now-end <= 14*86400:
                matches.append((end-start, end))
        if len(matches) < 3:
            continue
        residuals = [duration-elapsed for duration, _ in matches if duration > elapsed]
        if residuals:
            earliest, latest = now+min(residuals), now+max(residuals)
            if unfinished:
                earliest = max(earliest, stamp(run['observed_finish']['earliest']))
                latest = max(latest, stamp(run['observed_finish']['latest']))
            run['completion'] = window(earliest, latest, len(matches), now-max(end for _, end in matches))
            run['completion']['assumption'] = '동일 작업 구성의 이력과 현재 대기열을 반영한 조건부 시나리오'
        else:
            run['completion'] = unknown('overrun', '동일 구성 이력의 완료 시간을 초과했습니다')
    return {'observed_at': iso(now), 'source': raw.get('source', 'live'), 'viewer': raw.get('viewer'), 'coverage': coverage,
            'warnings': raw.get('warnings', []), 'pools': pools, 'runs': runs}
