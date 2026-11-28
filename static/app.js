'use strict';
const $ = id => document.getElementById(id);
const runControls = ['pr-filter', 'author-filter', 'mine-filter', 'group-by-author'];
const developPushLabel = 'develop 푸시';
const poolNames = {build: '빌드', general: '일반', light: '라이트'};
const categoryColors = {
  release: '#1b6f72',
  debug: '#4a9d98',
  shell: '#165f61',
  sql: '#2f8f83',
  sql_by_cci: '#6fb5a8',
  plan: '#3e6b89',
  medium: '#298074',
  collect: '#7aa89f',
  rerun: '#c4853b',
  other: '#94a3a3',
};
let snapshot = null;
let estimateUnavailable = null;
let openRuns = new Set();
let openPools = new Set();
let selectedPoolFilter = null;
for (const id of runControls) {
  const el = $(id), saved = localStorage.getItem(id);
  if (el.type === 'checkbox') el.checked = saved === 'true'; else el.value = saved || '';
  el.addEventListener('input', () => { localStorage.setItem(id, el.type === 'checkbox' ? el.checked : el.value); renderRuns(); });
}
$('clear').addEventListener('click', () => {
  for (const id of runControls) {
    const el = $(id);
    if (el.type === 'checkbox') el.checked = false; else el.value = '';
    localStorage.removeItem(id);
  }
  selectedPoolFilter = null;
  if (snapshot) renderPools();
  renderRuns();
});
function node(tag, text, cls) { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; }
function add(parent, tag, text, cls) { const e = node(tag, text, cls); parent.append(e); return e; }
function link(parent, text, url) { const a = add(parent, 'a', text); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a; }
function time(value) { if (!value) return '미확인'; const d = new Date(value); return new Intl.DateTimeFormat('ko-KR', {timeZone:'Asia/Seoul', month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit'}).format(d); }
function age(value) { const n = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000)); return n < 60 ? `${n}초 전` : n < 3600 ? `${Math.floor(n/60)}분 전` : `${Math.floor(n/3600)}시간 전`; }
function estimate(e) {
  if (!e || e.kind === 'unknown') return e?.text || '예상 불가';
  if (e.kind === 'observed') return `실제 ${time(e.at)}`;
  if (estimateUnavailable) return `예상 불가 · ${estimateUnavailable}`;
  return `예상 ${time(e.earliest)} ~ ${time(e.latest)} · 표본 ${e.samples}개 · 최신 ${Math.floor(e.sample_age_seconds/86400)}일 전`;
}
function togglePoolFilter(pool, category, label) {
  if (selectedPoolFilter && selectedPoolFilter.pool === pool && selectedPoolFilter.category === category) selectedPoolFilter = null;
  else selectedPoolFilter = {pool, category, label};
  renderPools();
  renderRuns();
}
function renderPools() {
  const target = $('pools'); target.replaceChildren();
  for (const p of snapshot.pools) {
    const card = add(target, 'article', null, 'pool'), head = add(card, 'div', null, 'pool-head');
    add(head, 'span', poolNames[p.name] || p.name, 'pool-name');
    add(head, 'span', p.label || p.name, 'pool-tag');
    const vals = add(card, 'div', null, 'pool-values');
    const metrics = [[p.running, '실행 중'], [p.waiting, '대기']];
    if (p.capacity > 0) metrics.push([`${Math.round((p.running / p.capacity) * 100)}%`, '사용률']);
    for (const [n, label] of metrics) { const item = add(vals, 'div'); add(item, 'strong', n); add(item, 'span', label); }
    const breakdown = p.breakdown || [];
    const bar = add(card, 'div', null, 'pool-bar');
    const denom = Math.max(p.capacity || 0, p.running || 0, 1);
    for (const cat of breakdown) {
      if (!cat.running) continue;
      const seg = add(bar, 'span', null, 'pool-bar-seg');
      seg.style.width = `${(cat.running / denom) * 100}%`;
      seg.style.background = categoryColors[cat.key] || categoryColors.other;
      seg.title = `${cat.label}: 실행 ${cat.running}`;
    }
    if (breakdown.length) {
      const list = add(card, 'div', null, 'pool-breakdown');
      for (const cat of breakdown) {
        const isSelected = selectedPoolFilter?.pool === p.name && selectedPoolFilter?.category === cat.key;
        const isZero = !cat.running && !cat.waiting;
        const btn = add(list, 'button', null, `pool-cat${isZero ? ' zero' : ''}${isSelected ? ' active' : ''}`);
        btn.type = 'button';
        btn.setAttribute('aria-pressed', String(isSelected));
        const left = add(btn, 'span', null, 'pool-cat-left');
        const dot = add(left, 'span', null, 'pool-dot');
        dot.style.background = categoryColors[cat.key] || categoryColors.other;
        add(left, 'span', cat.label, 'pool-cat-name');
        add(btn, 'span', `실행 ${cat.running} · 대기 ${cat.waiting}`, 'pool-cat-counts');
        btn.addEventListener('click', () => togglePoolFilter(p.name, cat.key, `${poolNames[p.name] || p.name} · ${cat.label}`));
      }
    }
    add(card, 'div', `설정 용량 ${p.capacity} · ${p.condition.text}`, `pool-foot ${p.condition.kind === 'unknown' ? 'warning' : ''}`);
    const entries = p.entries || [];
    const details = add(card, 'details', null, 'pool-entries');
    details.open = openPools.has(p.name);
    details.addEventListener('toggle', () => { if (details.open) openPools.add(p.name); else openPools.delete(p.name); });
    add(details, 'summary', entries.length ? `포함된 실행 ${entries.length}건` : '현재 점유/대기 실행 없음');
    if (entries.length) {
      const box = add(details, 'div', null, 'pool-entry-list');
      for (const entry of entries) {
        const row = add(box, 'div', null, 'pool-entry');
        const top = add(row, 'div', null, 'pool-entry-top');
        const who = add(top, 'div', null, 'pool-entry-who');
        if (entry.pr) {
          link(who, `#${entry.pr}`, `https://github.com/CUBRID/cubrid/pull/${entry.pr}`);
          who.append(document.createTextNode(' '));
        }
        if (entry.develop_push) add(who, 'span', developPushLabel, 'develop-push');
        else add(who, 'span', entry.author ? `@${entry.author}` : '작성자 미확인', 'pool-entry-author');
        if (entry.pr) {
          const fbtn = add(who, 'button', '보기', 'pool-filter-btn');
          fbtn.type = 'button';
          fbtn.title = `PR #${entry.pr}만 보기`;
          fbtn.addEventListener('click', () => {
            $('pr-filter').value = String(entry.pr);
            localStorage.setItem('pr-filter', String(entry.pr));
            renderRuns();
          });
        }
        const statText = `실행 ${entry.running} · 대기 ${entry.waiting}${entry.min_ahead != null ? ` · 앞 ${entry.min_ahead}` : ''}`;
        add(top, 'span', statText, 'pool-entry-stats');
        const cats = add(row, 'div', null, 'pool-entry-cats');
        for (const c of entry.categories || []) {
          const parts = [];
          if (c.running) parts.push(`실행 ${c.running}`);
          if (c.waiting) parts.push(`대기 ${c.waiting}`);
          add(cats, 'span', `${c.label} ${parts.join(' / ')}`, 'pool-entry-cat');
        }
      }
    }
  }
}
function fact(parent, label, value) { const box = add(parent, 'div'); add(box, 'div', label, 'fact-label'); add(box, 'div', value, 'fact-value'); }
function card(run) {
  const card = node('article', null, 'run'), top = add(card, 'div', null, 'run-top'), titlebox = add(top, 'div');
  const title = add(titlebox, 'div', null, 'run-title');
  if (run.pr) { link(title, `#${run.pr}`, `https://github.com/CUBRID/cubrid/pull/${run.pr}`); title.append(document.createTextNode('  ')); }
  title.append(document.createTextNode(run.title.replace(/^PR\s*#\d+\s*[·:-]?\s*/i, '')));
  const meta = add(titlebox, 'div', null, 'run-meta');
  if (run.develop_push) add(meta, 'span', developPushLabel, 'develop-push');
  else meta.append(`작성자 ${run.author || '미확인'} · `);
  meta.append(`시도 ${run.attempt} · 생성 ${time(run.created_at)}`);
  const completed = run.status === 'completed';
  const status = completed ? `완료 · ${run.conclusion || '결과 미확인'}` : run.jobs.some(j => j.state === 'running') ? '실행 중' : '대기 중';
  add(top, 'span', status, `status ${completed ? 'done' : status === '대기 중' ? 'waiting' : ''}`);
  const facts = add(card, 'div', null, 'facts');
  fact(facts, run.actual_start ? '실제 시작' : '시작 예상', run.actual_start ? time(run.actual_start) : estimate(run.next_start));
  const showObservedFinish = !completed && !estimateUnavailable && run.completion.kind !== 'window' && run.observed_finish.kind === 'window';
  if (completed) fact(facts, '전체 CI 완료', status);
  else if (showObservedFinish) fact(facts, '관측 작업 완료 예상', estimate(run.observed_finish));
  else {
    const completion = estimate(run.completion);
    const assumption = !estimateUnavailable && run.completion.assumption;
    fact(facts, '전체 CI 완료 예상', assumption ? `${completion} · ${assumption}` : completion);
  }
  const waits = run.jobs.filter(j => j.state === 'waiting');
  const block = waits.length ? waits.map(j => j.pool).filter((v,i,a) => a.indexOf(v) === i).join(', ') : '현재 대기 작업 없음';
  const ahead = waits.filter(j => j.ahead != null).map(j => j.ahead);
  fact(facts, '대기 풀 / 관측 작업 앞 순서', ahead.length ? `${block} · 최소 ${Math.min(...ahead)}개 앞 (모델)` : block);
  if (!completed) {
    const note = showObservedFinish ? `전체 CI 완료 · ${estimate(run.completion)}` : `관측 작업 완료 · ${estimate(run.observed_finish)}`;
    add(card, 'div', note, 'finish-note');
  }
  const stages = add(card, 'div', null, 'stages');
  for (const [name, counts] of Object.entries(run.stages)) {
    const parts = Object.entries(counts).map(([s,n]) => `${{done:'완료', running:'실행', waiting:'대기', skipped:'건너뜀', cancelled:'취소', unknown:'미확인'}[s] || s} ${n}`);
    add(stages, 'span', `${name} · ${parts.join(' / ')}`, 'stage');
  }
  if (!run.jobs_complete) add(stages, 'span', '작업 수집 불완전', 'stage');
  const bottom = add(card, 'div', null, 'run-bottom'), links = add(bottom, 'div', null, 'run-links');
  link(links, 'Actions 실행 ↗', `https://github.com/CUBRID/cubrid/actions/runs/${run.id}/attempts/${run.attempt}`);
  const details = add(bottom, 'details'); details.open = openRuns.has(`${run.id}:${run.attempt}`);
  details.addEventListener('toggle', () => { const key = `${run.id}:${run.attempt}`; if (details.open) openRuns.add(key); else openRuns.delete(key); });
  add(details, 'summary', `작업 상세 ${run.jobs.length}개`);
  const table = add(add(details, 'div', null, 'jobs'), 'table'), head = add(table, 'thead'), hr = add(head, 'tr');
  for (const h of ['작업', '상태', '풀', '시작 / 예상', '관측 작업 완료 예상', '모델상 앞선 작업']) add(hr, 'th', h);
  const body = add(table, 'tbody');
  for (const j of run.jobs) { const tr = add(body, 'tr'); for (const val of [j.name, `${j.state}${j.conclusion ? ` · ${j.conclusion}` : ''}`, j.pool, j.state === 'waiting' ? estimate(j.estimate) : j.state === 'running' || j.state === 'done' ? time(j.started_at) : '-', j.finish ? estimate(j.finish) : '-', j.ahead == null ? '-' : `${j.ahead}개`]) add(tr, 'td', val || '-'); }
  return card;
}
function renderAuthorGroups(target, runs) {
  const groups = new Map();
  for (const run of runs) {
    let label = '작성자 미확인';
    if (run.develop_push) label = developPushLabel;
    else if (run.author) label = `@${run.author}`;
    const key = label.toLowerCase();
    if (!groups.has(key)) groups.set(key, {label, runs:[]});
    groups.get(key).runs.push(run);
  }
  for (const {label, runs:groupRuns} of groups.values()) {
    const group = add(target, 'section', null, 'author-group');
    add(group, 'h4', `${label} · ${groupRuns.length}건`);
    for (const run of groupRuns) group.append(card(run));
  }
}
function renderActiveFilterChip() {
  const container = $('active-filters');
  container.replaceChildren();
  if (!selectedPoolFilter) {
    container.hidden = true;
    return;
  }
  container.hidden = false;
  const chip = add(container, 'span', null, 'filter-chip');
  add(chip, 'span', `풀 필터: ${selectedPoolFilter.label}`);
  const close = add(chip, 'button', '×');
  close.type = 'button';
  close.setAttribute('aria-label', '풀 필터 해제');
  close.addEventListener('click', () => {
    selectedPoolFilter = null;
    renderPools();
    renderRuns();
  });
}
function renderRuns() {
  if (!snapshot) return;
  renderActiveFilterChip();
  const pr = $('pr-filter').value.trim().replace(/^#/, ''), author = $('author-filter').value.trim().toLowerCase(), mine = $('mine-filter').checked;
  const viewer = snapshot.viewer?.toLowerCase();
  const matches = snapshot.runs.filter(run => {
    const login = run.author?.toLowerCase() || '';
    return (!pr || String(run.pr || '').includes(pr)) &&
      (!author || login.includes(author)) &&
      (!mine || (viewer && login === viewer && !run.develop_push));
  });
  let visibleCount = 0;
  for (const [id, active] of [['active',true], ['recent',false]]) {
    const target = $(id); target.replaceChildren();
    let list = matches.filter(r => (r.status !== 'completed') === active).sort((a,b) => (b.created_at || '').localeCompare(a.created_at || ''));
    if (active && selectedPoolFilter) {
      list = list.filter(r => r.jobs.some(j => j.pool === selectedPoolFilter.pool && j.category === selectedPoolFilter.category && (j.state === 'running' || j.state === 'waiting')));
    }
    visibleCount += list.length;
    if (!list.length) add(target, 'div', mine && !snapshot.viewer ? '현재 사용자를 확인할 수 없습니다.' : '조건에 맞는 실행이 없습니다.', 'empty');
    else if ($('group-by-author').checked) renderAuthorGroups(target, list);
    else for (const run of list) target.append(card(run));
  }
  $('count').textContent = `${visibleCount}건`;
}
async function refresh() {
  try {
    const response = await fetch('/api/snapshot', {cache:'no-store'}); if (!response.ok) throw Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (data.snapshot) {
      snapshot = data.snapshot;
      const stale = Date.now() - new Date(snapshot.observed_at).getTime() > 180000;
      estimateUnavailable = data.error ? '최근 갱신 실패' : !snapshot.coverage ? '활성 작업 수집 불완전' : stale ? '관측 데이터가 오래됨' : null;
      const dur = data.refresh?.duration_ms != null ? ` · 수집 ${(data.refresh.duration_ms / 1000).toFixed(1)}초` : '';
      $('freshness').textContent = `${snapshot.source === 'demo' ? 'DEMO' : 'LIVE'} · ${age(snapshot.observed_at)} 관측${dur}${snapshot.coverage ? '' : ' · 수집 불완전'}${data.loading ? ' · 갱신 중' : ''}`;
      $('freshness').title = `관측 ${time(snapshot.observed_at)} (서울)`;
      const alerts = $('alerts'); alerts.replaceChildren();
      if (snapshot.source === 'demo') add(alerts, 'div', 'DEMO · 실제 GitHub 데이터가 아닌 예시입니다.', 'alert');
      if (data.error) add(alerts, 'div', `갱신 실패. 이전 데이터를 표시합니다: ${data.error}`, 'alert');
      if (stale) add(alerts, 'div', data.loading ? '관측 데이터가 오래되었습니다. 최신 데이터를 수집하는 중이며 완료 전까지 예상은 표시되지 않습니다.' : '관측 데이터가 오래되었습니다. 예상은 현재 상황과 다를 수 있습니다.', 'alert');
      if (!snapshot.coverage || snapshot.warnings.length) add(alerts, 'div', `수집 주의: ${snapshot.warnings.slice(0, 4).join(' · ') || '활성 작업 수집 불완전'}${snapshot.warnings.length > 4 ? ` 외 ${snapshot.warnings.length-4}건` : ''}`, 'alert');
      renderPools(); renderRuns();
    } else if (data.error) { $('freshness').textContent = '데이터 수집 실패'; $('alerts').replaceChildren(node('div', `GitHub 데이터를 읽지 못했습니다: ${data.error}`, 'alert')); }
  } catch (e) { $('freshness').textContent = '연결 오류'; $('alerts').replaceChildren(node('div', `서버 연결 실패: ${e.message}`, 'alert')); }
  setTimeout(refresh, snapshot ? 10000 : 1500);
}
refresh();
