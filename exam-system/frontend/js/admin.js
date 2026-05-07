// Admin Command Center — High Performance optimized
const API = window.location.origin;
const WS_BASE = `ws://${window.location.host}`;

let adminToken = localStorage.getItem('admin_token') || '';
let currentSession = null;
let wsClient = null;
let pollTimer = null;
let draftQuestions = [];

const $ = id => document.getElementById(id);

// ── Boot ──────────────────────────────────────────────────────────────────────
if (adminToken) bootApp();
else showTokenScreen();

function showTokenScreen() {
  $('screen-token').style.display = 'flex';
  $('app').style.display = 'none';
}

$('token-submit').addEventListener('click', async () => {
  const t = $('token-input').value.trim();
  if (!t) return;
  adminToken = t;
  localStorage.setItem('admin_token', t);
  bootApp();
});

async function bootApp() {
  $('screen-token').style.display = 'none';
  $('app').style.display = 'flex';
  showScreen('sessions');
  await loadSessions();
  pollTimer = setInterval(loadSessions, 5000);
}

// ── Navigation ────────────────────────────────────────────────────────────────
function showScreen(name) {
  $('screen-sessions').style.display = name === 'sessions' ? 'block' : 'none';
  $('screen-control').style.display  = name === 'control'  ? 'grid'  : 'none';
  $('btn-back').style.display = name === 'control' ? 'block' : 'none';
}

$('btn-back').addEventListener('click', () => {
  if (wsClient) wsClient.close();
  currentSession = null;
  showScreen('sessions');
});

// ── Sessions ──────────────────────────────────────────────────────────────────
async function loadSessions() {
  try {
    const res = await fetch(`${API}/api/admin/sessions?token=${adminToken}`);
    if (res.status === 403) { showTokenScreen(); return; }
    const sessions = await res.json();
    renderSessionList(sessions);
  } catch (e) {}
}

function renderSessionList(sessions) {
  const el = $('session-list');
  el.innerHTML = '';
  sessions.forEach(s => {
    const card = document.createElement('div');
    card.className = `sess-card status-${s.status}`;
    card.innerHTML = `
      <div class="sess-title">${s.title}</div>
      <div class="sess-code">${s.session_code || '—'}</div>
      <div style="margin-top:12px; font-size:12px; color:var(--text-dim)">
        👥 ${s.student_count} Students · 📋 ${s.question_count} Qs
      </div>`;
    card.onclick = () => openSession(s.id);
    el.appendChild(card);
  });
}

// ── Control Logic ─────────────────────────────────────────────────────────────
async function openSession(sid) {
  try {
    const res = await fetch(`${API}/api/admin/sessions/${sid}?token=${adminToken}`);
    currentSession = await res.json();
    showScreen('control');
    connectWS(sid);
    syncUI(currentSession);
  } catch (e) { showToast('Error opening session', 'error'); }
}

function syncUI(sess) {
  $('ctrl-status-badge').textContent = sess.status.toUpperCase();
  $('live-conn').textContent = sess.connected_count || 0;
  
  // Hide all action buttons first
  const btns = ['btn-start', 'btn-next-q', 'btn-end-q', 'btn-pause', 'btn-resume', 'btn-export', 'btn-end'];
  btns.forEach(id => $(id).style.display = 'none');

  // Smart Visibility
  if (sess.status === 'waiting') {
    $('btn-start').style.display = 'flex';
  } else if (sess.status === 'active') {
    $('btn-next-q').style.display = 'flex';
    $('btn-end-q').style.display = 'flex';
    $('btn-pause').style.display = 'flex';
    $('btn-end').style.display = 'flex';
  } else if (sess.status === 'paused') {
    $('btn-resume').style.display = 'flex';
    $('btn-end').style.display = 'flex';
  } else if (sess.status === 'ended') {
    $('btn-export').style.display = 'flex';
  }

  if (sess.leaderboard) renderLeaderboard(sess.leaderboard);
}

function connectWS(sid) {
  if (wsClient) wsClient.close();
  wsClient = new ExamWSClient({
    url: `${WS_BASE}/ws/admin/${sid}?token=${adminToken}`,
    onOpen: () => { $('admin-conn').textContent = '● Live'; $('admin-conn').style.color = 'var(--success)'; },
    onClose: () => { $('admin-conn').textContent = '○ Reconnecting'; $('admin-conn').style.color = 'var(--warn)'; },
    onMessage: handleMsg
  });
  wsClient.connect();
}

function handleMsg(msg) {
  const { type, data } = msg;
  if (type === 'student_connected' || type === 'student_disconnected') {
    $('live-conn').textContent = data.connected_count;
    logActivity(`${data.name} ${type === 'student_connected' ? 'joined' : 'left'}`, 'info');
  } 
  if (type === 'question_push') {
    $('cur-q-text').textContent = `Q${data.index + 1}: ${data.text}`;
    $('cur-q-options').textContent = data.options.join(' | ');
    $('answer-stat-display').textContent = 'Answered: 0';
  }
  if (type === 'answer_stat') {
    $('answer-stat-display').textContent = `Answered: ${data.answered_count} / ${data.total_students}`;
  }
  if (type === 'leaderboard_update') {
    renderLeaderboard(data);
  }
  if (type === 'violation_alert') {
    logActivity(`⚠️ ${data.student_name}: ${data.violation_type}`, 'danger');
  }
  if (msg.status) {
    currentSession.status = msg.status;
    syncUI(currentSession);
  }
}

// ── Bulk Import Logic ────────────────────────────────────────────────────────
$('btn-parse-bulk').onclick = () => {
  const raw = $('bulk-import-area').value.trim();
  if (!raw) return showToast('Paste some CSV data first', 'error');

  const lines = raw.split('\n');
  let added = 0;
  
  lines.forEach(line => {
    const parts = line.split(',').map(p => p.trim());
    // Minimum: Question + 2 options + answer index = 4 parts
    if (parts.length >= 4) {
      const question = parts[0];
      const correctIdx = parseInt(parts[parts.length - 1]);
      const options = parts.slice(1, parts.length - 1);

      if (!isNaN(correctIdx) && question && options.length >= 2) {
        draftQuestions.push({
          text: question,
          options: options,
          correct_index: correctIdx,
          points: 10,
          time_limit: 30
        });
        added++;
        
        // Update the visual list
        const item = document.createElement('div');
        item.style = 'font-size:11px; padding:4px; border-bottom:1px solid var(--border); color:var(--text-dim)';
        item.textContent = `✅ Q: ${question.slice(0, 40)}...`;
        $('draft-list').appendChild(item);
      }
    }
  });

  if (added > 0) {
    showToast(`Successfully imported ${added} questions!`, 'success');
    $('bulk-import-area').value = '';
  } else {
    showToast('Could not parse any questions. Check format.', 'error');
  }
};

// ── Actions ──────────────────────────────────────────────────────────────────
async function perform(action) {
  try {
    const res = await fetch(`${API}/api/admin/sessions/${currentSession.id}/${action}?token=${adminToken}`, { method: 'POST' });
    const data = await res.json();
    if (data.status) {
      currentSession.status = data.status;
      syncUI(currentSession);
    }
  } catch (e) { showToast('Action failed', 'error'); }
}

$('btn-start').onclick = () => perform('start');
$('btn-next-q').onclick = () => perform('next_question');
$('btn-end-q').onclick = () => perform('end_question');
$('btn-pause').onclick = () => perform('pause');
$('btn-resume').onclick = () => perform('resume');
$('btn-end').onclick = () => { if(confirm('End exam?')) perform('end'); };
$('btn-export').onclick = () => window.open(`${API}/api/admin/sessions/${currentSession.id}/export?token=${adminToken}`);

// ── Add Live Q ────────────────────────────────────────────────────────────────
$('btn-add-live-q').onclick = async () => {
  const text = $('q-text-input').value.trim();
  const opts = [$('opt0').value, $('opt1').value, $('opt2').value, $('opt3').value].filter(Boolean);
  if (!text || opts.length < 2) return showToast('Fill question + 2 options', 'error');
  
  await fetch(`${API}/api/admin/sessions/${currentSession.id}/questions`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      admin_token: adminToken,
      questions: [{ text, options: opts, correct_index: parseInt($('correct-idx').value) || 0 }]
    })
  });
  showToast('Question added!');
  ['q-text-input','opt0','opt1','opt2','opt3'].forEach(id => $(id).value = '');
};

// ── Helpers ───────────────────────────────────────────────────────────────────
function renderLeaderboard(entries) {
  const el = $('admin-leaderboard');
  el.innerHTML = '';
  entries.slice(0, 10).forEach(e => {
    const row = document.createElement('div');
    row.className = 'lb-row';
    row.innerHTML = `<span class="lb-rank">#${e.rank}</span><span class="lb-name">${e.name}</span><span style="font-weight:700">${e.score}</span>`;
    el.appendChild(row);
  });
}

function logActivity(txt, lv) {
  const row = document.createElement('div');
  row.className = `log-row ${lv}`;
  row.textContent = `[${new Date().toLocaleTimeString()}] ${txt}`;
  $('violation-log').prepend(row);
}

$('create-session-btn').onclick = () => $('create-modal').classList.remove('hidden');
$('cancel-create').onclick = () => $('create-modal').classList.add('hidden');

$('btn-hard-reset').onclick = () => {
  localStorage.clear();
  window.location.reload();
};

function showToast(m, t='info') {
  const toast = document.createElement('div');
  toast.style = `background:var(--panel); padding:12px 20px; border-radius:12px; border-left:4px solid var(--accent); margin-bottom:8px; box-shadow:0 10px 30px rgba(0,0,0,0.5)`;
  toast.textContent = m;
  $('toast-container').appendChild(toast);
  setTimeout(() => toast.remove(), 3000);
}
