// ExamLAN Admin — Complete, all features wired
'use strict';

const API     = window.location.origin;
const WS_BASE = `ws://${window.location.host}`;

let token    = localStorage.getItem('admin_token') || '';
let session  = null;   // full session object from API
let ws       = null;
let pollId   = null;
let draftQs  = [];     // questions staged before create
let studentsList = []; // list of students for the grid
let selectedStudentToUnlock = null;

const $  = id  => document.getElementById(id);
const qs = sel => document.querySelector(sel);

// ════════════════════════════════════════════════════════════════════
//  BOOT
// ════════════════════════════════════════════════════════════════════
(function init() {
  if (token) bootApp();
  else showEl('screen-token');
})();

$('token-submit').onclick = async () => {
  const t = $('token-input').value.trim();
  if (!t) return;
  // Quick verify
  try {
    const r = await fetch(`${API}/api/admin/sessions?token=${t}`);
    if (r.status === 403) return toast('Wrong token', 'err');
    token = t;
    localStorage.setItem('admin_token', t);
    bootApp();
  } catch { toast('Server unreachable', 'err'); }
};
$('token-input').onkeydown = e => { if (e.key === 'Enter') $('token-submit').click(); };

async function bootApp() {
  hideEl('screen-token');
  showEl('app', 'flex');
  showScreen('sessions');
  await loadSessions();
  pollId = setInterval(loadSessions, 6000);
  $('ctrl-url').textContent = window.location.host;
}

// ════════════════════════════════════════════════════════════════════
//  NAVIGATION
// ════════════════════════════════════════════════════════════════════
function showScreen(name) {
  $('screen-sessions').style.display = name === 'sessions' ? 'block' : 'none';
  $('screen-control').style.display  = name === 'control'  ? 'grid'  : 'none';
  $('btn-back').style.display = name === 'control' ? 'block' : 'none';
}

$('btn-back').onclick = () => {
  if (ws) { ws.close(); ws = null; }
  session = null;
  showScreen('sessions');
  loadSessions();
};

$('btn-reset').onclick = async () => {
  if (!confirm('Clear all local data and reload?')) return;
  if ('serviceWorker' in navigator)
    (await navigator.serviceWorker.getRegistrations()).forEach(r => r.unregister());
  localStorage.clear();
  location.reload();
};

// ════════════════════════════════════════════════════════════════════
//  SESSIONS LIST
// ════════════════════════════════════════════════════════════════════
async function loadSessions() {
  try {
    const r = await fetch(`${API}/api/admin/sessions?token=${token}`);
    if (r.status === 403) { token = ''; localStorage.removeItem('admin_token'); location.reload(); return; }
    renderSessionList(await r.json());
  } catch {}
}

function renderSessionList(list) {
  const el = $('session-list');
  if (!list.length) {
    el.innerHTML = '<div class="empty-state">No sessions yet. Create your first exam →</div>';
    return;
  }
  el.innerHTML = '';
  list.forEach(s => {
    const c = document.createElement('div');
    c.className = 'sess-card';
    const dotCls = { active: 'active', ended: 'ended', paused: 'paused' }[s.status] || '';
    c.innerHTML = `
      <div class="sess-card-status">
        <span class="sess-status-dot ${dotCls}"></span>${s.status.toUpperCase()}
      </div>
      <div class="sess-card-title">${s.title}</div>
      <div class="sess-card-code">${s.session_code || '—'}</div>
      <div class="sess-card-meta">
        <span>📋 ${s.question_count} questions</span>
        <span>👥 ${s.student_count} students</span>
        <span>${new Date(s.created_at).toLocaleDateString()}</span>
      </div>`;
    c.onclick = () => openSession(s.id);
    el.appendChild(c);
  });
}

// ════════════════════════════════════════════════════════════════════
//  CREATE SESSION MODAL
// ════════════════════════════════════════════════════════════════════
$('btn-new-session').onclick = () => {
  draftQs = [];
  renderDraftList();
  $('modal-create').style.display = 'flex';
};
$('btn-cancel-create').onclick = () => { $('modal-create').style.display = 'none'; };
$('modal-create').onclick = e => { if (e.target === $('modal-create')) $('modal-create').style.display = 'none'; };

// Single question add
$('btn-add-q').onclick = () => {
  const text    = $('m-q').value.trim();
  const opts    = [$('m-a').value, $('m-b').value, $('m-c').value, $('m-d').value].map(v => v.trim()).filter(Boolean);
  const correct = parseInt($('m-correct').value);
  const pts     = parseFloat($('m-pts').value) || 10;

  if (!text)             return toast('Enter question text', 'err');
  if (opts.length < 2)   return toast('Enter at least 2 options', 'err');
  if (isNaN(correct) || correct >= opts.length) return toast('Correct index out of range', 'err');

  draftQs.push({ text, options: opts, correct_index: correct, points: pts, time_limit: parseInt($('m-time').value) || 30 });
  renderDraftList();
  // Clear fields
  [$('m-q'), $('m-a'), $('m-b'), $('m-c'), $('m-d')].forEach(el => el.value = '');
  $('m-q').focus();
};

// Bulk import
$('btn-parse-bulk').onclick = () => {
  const raw = $('bulk-area').value.trim();
  if (!raw) return toast('Paste CSV data first', 'err');
  const lines = raw.split('\n').filter(l => l.trim());
  let added = 0;
  lines.forEach(line => {
    const parts = line.split(',').map(p => p.trim());
    if (parts.length < 4) return;
    const text      = parts[0];
    const correct   = parseInt(parts[parts.length - 1]);
    const options   = parts.slice(1, parts.length - 1);
    if (!text || isNaN(correct) || options.length < 2 || correct >= options.length) return;
    draftQs.push({ text, options, correct_index: correct, points: 10,
      time_limit: parseInt($('m-time').value) || 30 });
    added++;
  });
  renderDraftList();
  if (added) { toast(`Imported ${added} question${added > 1 ? 's' : ''}`, 'ok'); $('bulk-area').value = ''; }
  else toast('No valid lines found. Check format.', 'err');
};

function renderDraftList() {
  const el = $('draft-list');
  $('q-count-label').textContent = `${draftQs.length} question${draftQs.length !== 1 ? 's' : ''}`;
  if (!draftQs.length) { el.innerHTML = ''; return; }
  el.innerHTML = '';
  draftQs.forEach((q, i) => {
    const d = document.createElement('div');
    d.className = 'q-draft-item';
    d.innerHTML = `
      <span class="q-draft-num">Q${i + 1}</span>
      <span class="q-draft-text">${q.text}</span>
      <span class="q-draft-del" data-i="${i}" title="Remove">×</span>`;
    el.appendChild(d);
  });
  el.querySelectorAll('.q-draft-del').forEach(btn => {
    btn.onclick = () => { draftQs.splice(+btn.dataset.i, 1); renderDraftList(); };
  });
}

// Submit create
$('btn-submit-create').onclick = async () => {
  const title = $('m-title').value.trim();
  if (!title) return toast('Session title is required', 'err');

  $('btn-submit-create').disabled = true;
  $('btn-submit-create').textContent = 'Creating…';
  try {
    const r = await fetch(`${API}/api/admin/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title,
        session_code: $('m-code').value.trim().toUpperCase() || null,
        admin_token: token,
        per_question_time: parseInt($('m-time').value) || 30,
        questions: draftQs,
        randomize_questions: false,
        randomize_options: false,
        pacing_mode: $('m-pacing').value
      }),
    });
    const data = await r.json();
    if (!r.ok) { toast(data.detail || 'Create failed', 'err'); return; }
    toast(`✅ Created — code: ${data.session_code}`, 'ok');
    $('modal-create').style.display = 'none';
    draftQs = [];
    [$('m-title'), $('m-code')].forEach(el => el.value = '');
    await loadSessions();
  } catch { toast('Network error', 'err'); }
  finally {
    $('btn-submit-create').disabled = false;
    $('btn-submit-create').textContent = 'Create Session →';
  }
};

// ════════════════════════════════════════════════════════════════════
//  OPEN SESSION → CONTROL PANEL
// ════════════════════════════════════════════════════════════════════
async function openSession(id) {
  try {
    const r = await fetch(`${API}/api/admin/sessions/${id}?token=${token}`);
    if (!r.ok) { toast('Failed to load session', 'err'); return; }
    session = await r.json();
    showScreen('control');
    applySession(session);
    connectWS(id);
  } catch { toast('Error loading session', 'err'); }
}

function applySession(s) {
  $('ctrl-title').textContent = s.title;
  $('ctrl-code').textContent  = s.session_code || '—';
  $('stat-students').textContent = s.connected_count || 0;
  $('stat-qs').textContent = (s.questions || []).length;
  $('stat-answered').textContent = '—';
  setChip(s.status);
  setButtons(s.status);
  
  if (s.status === 'waiting') {
    renderWaitingQuestions(s.questions || []);
  } else {
    $('waiting-questions-list').style.display = 'none';
  }
  
  renderLeaderboard(s.leaderboard || []);
  studentsList = s.students || [];
  renderStudentGrid();
}

function setChip(status) {
  const c = $('ctrl-chip');
  c.textContent = status.toUpperCase();
  c.className = `status-chip ${status}`;
}

function setButtons(status) {
  // All hidden first
  ['btn-start','btn-pause','btn-resume','btn-export','btn-end'].forEach(id => $(id).style.display = 'none');
  if (status === 'waiting') {
    show('btn-start');
  } else if (status === 'active') {
    show('btn-pause'); show('btn-end');
  } else if (status === 'paused') {
    show('btn-resume'); show('btn-end');
  } else if (status === 'ended') {
    show('btn-export');
  }
  function show(id) { $(id).style.display = 'flex'; }
}

// ════════════════════════════════════════════════════════════════════
//  CONTROL ACTIONS
// ════════════════════════════════════════════════════════════════════
async function action(act) {
  const btn = { start: 'btn-start', end: 'btn-end' }[act];
  if (btn) $(btn).disabled = true;
  try {
    const r = await fetch(`${API}/api/admin/sessions/${session.id}/${act}?token=${token}`, { method: 'POST' });
    const d = await r.json();
    if (!r.ok) { toast(d.detail || `${act} failed`, 'err'); return; }
    if (d.status) { session.status = d.status; setChip(d.status); setButtons(d.status); }
    const labels = { start: '▶ Exam started', pause: '⏸ Paused', resume: '▶ Resumed', end: '■ Exam ended' };
    toast(labels[act] || 'Done', 'ok');
  } catch { toast('Request failed', 'err'); }
  finally { if (btn) $(btn).disabled = false; }
}

$('btn-start').onclick  = () => action('start');
$('btn-pause').onclick  = () => action('pause');
$('btn-resume').onclick = () => action('resume');
$('btn-end').onclick    = () => { if (confirm('End the exam for all students?')) action('end'); };
$('btn-export').onclick = () => window.open(`${API}/api/admin/sessions/${session.id}/export?token=${token}`);

$('btn-delete-session').onclick = async () => {
  if (!confirm('Are you sure you want to permanently delete this session?')) return;
  try {
    const r = await fetch(`${API}/api/admin/sessions/${session.id}?token=${token}`, { method: 'DELETE' });
    if (!r.ok) throw new Error();
    toast('Session deleted', 'ok');
    $('btn-back').click();
  } catch {
    toast('Failed to delete session', 'err');
  }
};

// Add live question
$('btn-add-live-q').onclick = async () => {
  const text    = $('lq-text').value.trim();
  const opts    = [$('lq-a').value, $('lq-b').value, $('lq-c').value, $('lq-d').value].map(v => v.trim()).filter(Boolean);
  const correct = parseInt($('lq-correct').value);
  const tl      = parseInt($('lq-time').value) || 30;
  if (!text || opts.length < 2 || isNaN(correct) || correct >= opts.length)
    return toast('Fill question, 2+ options, valid correct index', 'err');
  try {
    const r = await fetch(`${API}/api/admin/sessions/${session.id}/questions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ admin_token: token, questions: [{ text, options: opts, correct_index: correct, points: 10, time_limit: tl }] }),
    });
    if (!r.ok) { toast('Failed to add question', 'err'); return; }
    [$('lq-text'), $('lq-a'), $('lq-b'), $('lq-c'), $('lq-d')].forEach(el => el.value = '');
    $('stat-qs').textContent = parseInt($('stat-qs').textContent) + 1;
    if(session && session.questions) {
      session.questions.push({ text, options: opts, correct_index: correct });
      if(session.status === 'waiting') renderWaitingQuestions(session.questions);
    }
    toast('Question added to session', 'ok');
  } catch { toast('Network error', 'err'); }
};

// ════════════════════════════════════════════════════════════════════
//  ADMIN WEBSOCKET
// ════════════════════════════════════════════════════════════════════
function connectWS(sid) {
  if (ws) ws.close();
  ws = new ExamWSClient({
    url: `${WS_BASE}/ws/admin/${sid}?token=${token}`,
    onOpen:    () => { const d = $('conn-dot'); d.textContent = 'Live'; d.classList.add('live'); },
    onClose:   () => { const d = $('conn-dot'); d.textContent = 'Reconnecting…'; d.classList.remove('live'); },
    onMessage: handleWS,
  });
  ws.connect();
}

let totalStudents = 0;
let answeredCount = 0;

function handleWS(msg) {
  const { type, data } = msg;

  if (type === 'student_connected') {
    totalStudents = data.connected_count;
    $('stat-students').textContent = totalStudents;
    logActivity(`${data.name} connected`, 'info');
    
    // Refresh full student list to get everything
    if (session) {
      fetch(`${API}/api/admin/sessions/${session.id}?token=${token}`)
        .then(r => r.json())
        .then(s => { studentsList = s.students || []; renderStudentGrid(); })
        .catch(console.error);
    }
  }

  if (type === 'student_disconnected') {
    totalStudents = data.connected_count;
    $('stat-students').textContent = totalStudents;
    logActivity(`${data.name} left`, 'warn');
  }

  if (type === 'question_push') {
    answeredCount = 0;
    const card = $('cur-q-card');
    card.classList.add('has-q');
    $('cur-q-text').textContent = `Q${data.index + 1} / ${data.total} — ${data.text}`;
    $('cur-q-options').innerHTML = '';
    data.options.forEach((opt, i) => {
      const span = document.createElement('span');
      span.className = `cur-q-opt${i === data.correct_index ? ' correct' : ''}`;
      span.textContent = `${['A','B','C','D'][i]}. ${opt}`;
      $('cur-q-options').appendChild(span);
    });
    $('ans-prog-wrap').style.display = 'block';
    $('ans-prog').style.width = '0%';
    $('stat-answered').textContent = `0 / ${data.total_students || totalStudents}`;
    setChip('active');
    setButtons('active');
    if (session) session.status = 'active';
  }

  if (type === 'answer_stat') {
    answeredCount = data.answered_count;
    const total = data.total_students || totalStudents || 1;
    $('stat-answered').textContent = `${answeredCount} / ${total}`;
    $('ans-prog').style.width = `${(answeredCount / total * 100).toFixed(0)}%`;
  }

  if (type === 'leaderboard_update') {
    renderLeaderboard(msg.data || data);
  }

  if (type === 'violation_alert') {
    logActivity(`⚠ ${data.student_name}: ${data.violation_type} (Strike ${data.strike_count})`, 'danger');
    const st = studentsList.find(s => s.id === data.student_id);
    if (st) {
      st.strike_count = data.strike_count;
      renderStudentGrid();
    }
  }

  if (type === 'session_start') {
    $('waiting-questions-list').style.display = 'none';
    setChip('active'); setButtons('active'); if (session) session.status = 'active';
  }
  if (type === 'pause') {
    setChip('paused'); setButtons('paused'); if (session) session.status = 'paused';
  }
  if (type === 'resume') {
    setChip('active'); setButtons('active'); if (session) session.status = 'active';
  }
  if (type === 'exam_end') {
    setChip('ended'); setButtons('ended'); if (session) session.status = 'ended';
    if (data?.leaderboard) renderLeaderboard(data.leaderboard);
    toast('Exam ended — export CSV to save results', 'ok');
  }
}

// ════════════════════════════════════════════════════════════════════
//  RIGHT PANEL TABS & GRID
// ════════════════════════════════════════════════════════════════════
document.querySelectorAll('.rp-tab').forEach(tab => {
  tab.onclick = () => {
    document.querySelectorAll('.rp-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    const pane = tab.dataset.pane;
    $('pane-lb').style.display       = pane === 'lb'       ? 'block' : 'none';
    $('pane-students').style.display = pane === 'students' ? 'block' : 'none';
    $('pane-activity').style.display = pane === 'activity' ? 'block' : 'none';
  };
});

function renderStudentGrid() {
  const el = $('students-grid');
  if (!el) return;
  if (!studentsList.length) { el.innerHTML = '<div style="color:var(--text3);font-size:14px">No students yet...</div>'; return; }
  el.innerHTML = '';
  
  studentsList.forEach(s => {
    const card = document.createElement('div');
    const v = s.strike_count || 0;
    const vClass = v >= 3 ? 'v-3' : (v > 0 ? `v-${v}` : 'v-0');
    card.className = `stu-card ${vClass}`;
    
    card.innerHTML = `
      <div class="stu-card-name">${s.name}</div>
      <div style="font-size:11px; color:var(--text3)">${s.roll_number || ''}</div>
      <div class="stu-card-viol ${vClass}">${v >= 3 ? 'LOCKED' : (v + ' Strikes')}</div>
    `;
    
    card.onclick = () => {
      selectedStudentToUnlock = s;
      $('unlock-name').textContent = `${s.name} (${s.roll_number})`;
      $('u-reduce').value = "0";
      $('modal-unlock').style.display = 'flex';
    };
    
    el.appendChild(card);
  });
}

// Unlock Modal actions
$('btn-cancel-unlock').onclick = () => { $('modal-unlock').style.display = 'none'; };
$('modal-unlock').onclick = e => { if (e.target === $('modal-unlock')) $('modal-unlock').style.display = 'none'; };

$('btn-submit-unlock').onclick = async () => {
  if (!selectedStudentToUnlock) return;
  const reduceMarks = parseFloat($('u-reduce').value) || 0;
  
  $('btn-submit-unlock').disabled = true;
  $('btn-submit-unlock').textContent = 'Unlocking…';
  
  try {
    const r = await fetch(`${API}/api/admin/sessions/${session.id}/unlock_student?student_id=${selectedStudentToUnlock.id}&reduce_marks=${reduceMarks}&token=${token}`, { method: 'POST' });
    if (!r.ok) { toast('Failed to unlock', 'err'); return; }
    toast(`${selectedStudentToUnlock.name} unlocked!`, 'ok');
    
    // Update local list
    selectedStudentToUnlock.strike_count = 0;
    selectedStudentToUnlock.score = Math.max(0, (selectedStudentToUnlock.score || 0) - reduceMarks);
    renderStudentGrid();
    
    $('modal-unlock').style.display = 'none';
  } catch {
    toast('Network error', 'err');
  } finally {
    $('btn-submit-unlock').disabled = false;
    $('btn-submit-unlock').textContent = 'Unlock →';
  }
};

// ════════════════════════════════════════════════════════════════════
//  RENDER HELPERS
// ════════════════════════════════════════════════════════════════════
const medals = ['🥇', '🥈', '🥉'];

function formatTime(sec) {
  if (!sec) return '';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s < 10 ? '0' : ''}${s}`;
}

function renderLeaderboard(entries) {
  const el = $('lb-list');
  if (!entries.length) { el.innerHTML = '<div style="color:var(--text3);font-size:14px;text-align:center;padding:20px 0">Rankings appear after exam begins…</div>'; return; }
  el.innerHTML = '';
  entries.forEach(e => {
    const row = document.createElement('div');
    row.style = 'display:flex; align-items:center; background:var(--surface2); padding:16px 20px; border-radius:12px; border:1px solid var(--border); margin-bottom:8px';
    const medal = medals[e.rank - 1] || null;
    row.innerHTML = `
      <div style="font-size:24px; font-weight:900; width:48px; color:${e.rank <= 3 ? 'var(--accent)' : 'var(--text3)'}">${medal || '#' + e.rank}</div>
      <div style="flex:1; min-width:0">
        <div style="font-size:16px; font-weight:800; white-space:nowrap; overflow:hidden; text-overflow:ellipsis">${e.name}</div>
        <div style="font-size:12px; color:var(--text3)">${e.roll_number}</div>
      </div>
      <div style="font-size:20px; font-weight:900; color:var(--accent); margin-right:16px">${(e.score || 0).toFixed(1)}</div>
      <div style="font-size:14px; font-weight:700; color:var(--text3); font-family:monospace">${formatTime(e.time_taken)}</div>
    `;
    el.appendChild(row);
  });
}

function renderWaitingQuestions(qs) {
  const el = $('waiting-questions-list');
  el.style.display = 'block';
  if (!qs.length) { el.innerHTML = 'No questions added yet.'; return; }
  el.innerHTML = '';
  qs.forEach((q, i) => {
    const d = document.createElement('div');
    d.style.marginBottom = '12px';
    let optsHtml = q.options.map((opt, j) => {
      let isCorrect = (j === q.correct_index);
      return `<span style="padding:2px 6px; border-radius:4px; margin-right:6px; background:${isCorrect?'rgba(16,185,129,.15)':'var(--s)'}; color:${isCorrect?'var(--green)':'var(--text3)'}; border:1px solid ${isCorrect?'var(--green)':'var(--border)'}">${['A','B','C','D'][j]}. ${opt}</span>`;
    }).join('');
    d.innerHTML = `<strong style="color:var(--text);font-size:14px">Q${i+1}: ${q.text}</strong><div style="margin-top:6px">${optsHtml}</div>`;
    el.appendChild(d);
  });
}

function logActivity(text, level = 'info') {
  const el = $('activity-list');
  const ph = el.querySelector('div[style]');
  if (ph) ph.remove();
  const row = document.createElement('div');
  row.className = 'act-entry';
  row.innerHTML = `<div class="act-time">${new Date().toLocaleTimeString()}</div>
    <div class="act-text ${level}">${text}</div>`;
  el.prepend(row);
  if (el.children.length > 100) el.lastChild.remove();
}

// ════════════════════════════════════════════════════════════════════
//  UTILS
// ════════════════════════════════════════════════════════════════════
function showEl(id, d = 'block') { $(id).style.display = d; }
function hideEl(id)              { $(id).style.display = 'none'; }

function toast(msg, type = 'info') {
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.textContent = msg;
  $('toasts').appendChild(t);
  setTimeout(() => t.remove(), 4000);
}
