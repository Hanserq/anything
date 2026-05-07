// ExamLAN Student Portal — Fixed & matching admin design
'use strict';

const API     = window.location.origin;
const WS_BASE = `ws://${window.location.host}`;

let studentId  = null;
let sessionId  = null;
let ws         = null;
let timerLoop  = null;
let currentQ   = null;
let overlay    = null;   // wait-for-next DOM element

const $ = id => document.getElementById(id);

// ── Screen management ─────────────────────────────────────────────────────────
function show(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  $(id).classList.add('active');
}

// ── Connection indicator ──────────────────────────────────────────────────────
function setConn(live) {
  const p = $('conn-pill');
  $('conn-label').textContent = live ? 'Live' : 'Reconnecting…';
  p.classList.toggle('live', !!live);
}

// ── Join ──────────────────────────────────────────────────────────────────────
$('btn-join').onclick = doJoin;
['inp-name', 'inp-roll', 'inp-code'].forEach(id =>
  $(id).addEventListener('keydown', e => { if (e.key === 'Enter') doJoin(); })
);

async function doJoin() {
  const name = $('inp-name').value.trim();
  const roll = $('inp-roll').value.trim();
  const code = $('inp-code').value.trim();
  if (!name) { toast('Enter your full name', 'err'); return; }
  if (!roll) { toast('Enter your roll number', 'err'); return; }
  if (!code) { toast('Enter the join code from your teacher', 'err'); return; }

  $('btn-join').disabled = true;
  $('btn-join').textContent = 'Joining…';
  try {
    const r = await fetch(`${API}/api/student/join`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: code, name, roll_number: roll }),
    });
    const d = await r.json();
    if (!r.ok) { toast(d.detail || 'Join failed — check the code', 'err'); return; }

    studentId = d.student_id;
    sessionId = d.session_id;   // server returns resolved UUID
    localStorage.setItem('exam_sid', studentId);
    localStorage.setItem('exam_sess', sessionId);
    localStorage.setItem('exam_name', d.name);

    $('wait-name').textContent = `You're in, ${d.name}!`;
    $('wait-code').textContent = `Session: ${d.session_title || code}`;
    show('screen-waiting');
    connectWS();
  } catch { toast('Server unreachable — check Wi-Fi', 'err'); }
  finally {
    $('btn-join').disabled = false;
    $('btn-join').textContent = 'Join Exam →';
  }
}

// ── WebSocket ─────────────────────────────────────────────────────────────────
function connectWS() {
  if (ws) ws.close();
  ws = new ExamWSClient({
    url: `${WS_BASE}/ws/student/${sessionId}?student_id=${studentId}`,
    onOpen:    handleOpen,
    onMessage: handleMsg,
    onClose:   () => setConn(false),
  });
  ws.connect();
}

async function handleOpen() {
  setConn(true);
  // Sync cached offline answers
  try {
    await ExamDB.open();
    const pending = await ExamDB.getPendingAnswers();
    if (pending.length) {
      ws.send({ type: 'sync_cached', data: { answers: pending } });
      await ExamDB.clearPendingAnswers();
    }
  } catch {}
}

function handleMsg(msg) {
  const { type, data } = msg;
  switch (type) {
    case 'connected':
      if (data.session_status === 'active' && data.current_question) {
        renderQuestion(data.current_question, data.current_question.elapsed || 0);
      } else {
        show('screen-waiting');
      }
      break;
    case 'session_start':
      removeOverlay();
      show('screen-waiting');
      toast('Exam starting!', 'ok');
      break;
    case 'question_push':
      removeOverlay();
      renderQuestion(data, 0);
      break;
    case 'answer_ack':
      handleAck(data);
      break;
    case 'question_end':
      // Time expired — reveal correct before overlay
      clearTimer();
      revealCorrect(data.correct_index);
      setTimeout(() => showWaitOverlay(false, 0), 1500);
      break;
    case 'exam_locked':
      clearTimer(); removeOverlay();
      $('lock-msg').textContent = data.message || 'Your exam has been locked.';
      show('screen-locked');
      break;
    case 'pause':
      toast('⏸ Exam paused by teacher', 'info');
      break;
    case 'resume':
      toast('▶ Exam resumed', 'ok');
      break;
    case 'exam_end':
      clearTimer(); removeOverlay();
      showResults((data || msg.data)?.leaderboard || []);
      break;
  }
}

// ── Question rendering ─────────────────────────────────────────────────────────
function renderQuestion(q, elapsed) {
  currentQ = q;
  show('screen-question');
  $('q-badge').textContent = `Q ${q.index + 1} / ${q.total}`;
  $('q-text').textContent = q.text;

  const grid = $('options-grid');
  grid.innerHTML = '';
  const LABELS = ['A','B','C','D','E','F'];
  q.options.forEach((opt, i) => {
    const btn = document.createElement('button');
    btn.className = 'opt-btn';
    btn.dataset.label = LABELS[i] || String(i + 1);
    btn.dataset.idx = i;
    btn.textContent = opt;
    if (q.already_answered) {
      btn.disabled = true;
    } else {
      btn.onclick = () => pickOption(i, btn);
    }
    grid.appendChild(btn);
  });

  if (q.time_limit > 0) {
    startTimer(q.time_limit, elapsed, q.start_time);
  } else {
    $('timer-num').textContent = '∞';
    $('timer-fill').style.width = '100%';
  }
}

function pickOption(idx, btn) {
  if (document.querySelector('.opt-btn.selected')) return; // already picked
  // Lock all options
  document.querySelectorAll('.opt-btn').forEach(b => { b.disabled = true; });
  btn.classList.add('selected');

  const timeTaken = (currentQ.time_limit > 0 && currentQ.start_time)
    ? Math.min(currentQ.time_limit, Date.now() / 1000 - currentQ.start_time)
    : 0;

  const payload = {
    type: 'submit_answer',
    data: { question_id: currentQ.question_id, selected_option: idx, time_taken: timeTaken },
  };

  if (ws?.isConnected) {
    ws.send(payload);
  } else {
    // Cache offline
    ExamDB.queueAnswer(payload.data).catch(() => {});
    toast('Saved offline — will sync on reconnect', 'info');
    // Show overlay anyway after 2s
    setTimeout(() => showWaitOverlay(false, 0), 2000);
  }
}

function handleAck({ is_correct, score_awarded, question_id }) {
  if (!currentQ || currentQ.question_id !== question_id) return;
  clearTimer();

  // Mark the selected option correct/wrong
  const sel = document.querySelector('.opt-btn.selected');
  if (sel) sel.classList.add(is_correct ? 'correct' : 'wrong');

  // 2 seconds later — show the wait overlay with score
  setTimeout(() => showWaitOverlay(is_correct, score_awarded ?? 0), 2000);
}

function revealCorrect(correctIdx) {
  document.querySelectorAll('.opt-btn').forEach((b, i) => {
    b.disabled = true;
    if (i === correctIdx && !b.classList.contains('correct')) {
      b.classList.add('correct-reveal');
    }
  });
}

// ── Wait overlay ──────────────────────────────────────────────────────────────
function showWaitOverlay(isCorrect, score) {
  removeOverlay();
  const div = document.createElement('div');
  div.className = 'wait-overlay';
  div.id = 'wait-overlay';

  // Score badge — always show something
  const badge = document.createElement('div');
  badge.className = `score-badge ${isCorrect ? 'correct' : 'wrong'}`;
  if (isCorrect) {
    badge.textContent = score > 0 ? `+${Number(score).toFixed(1)}` : '✓';
  } else {
    badge.textContent = '✗';
  }
  div.appendChild(badge);

  const p = document.createElement('p');
  p.textContent = '⏳ Waiting for next question…';
  div.appendChild(p);

  document.body.appendChild(div);
  overlay = div;
}

function removeOverlay() {
  if (overlay) { overlay.remove(); overlay = null; }
  const old = document.getElementById('wait-overlay');
  if (old) old.remove();
}

// ── Timer ─────────────────────────────────────────────────────────────────────
function startTimer(limit, elapsed, serverStartTime) {
  clearTimer();
  const fill = $('timer-fill');
  const num  = $('timer-num');

  function tick() {
    const rem = Math.max(0, limit - (Date.now() / 1000 - serverStartTime));
    const pct = (rem / limit) * 100;
    num.textContent = Math.ceil(rem);
    fill.style.width = pct + '%';

    if (rem < 8) {
      fill.style.background = 'var(--red)';
      num.className = 'timer-num urgent';
    } else if (pct < 40) {
      fill.style.background = 'var(--accent)';
      num.className = 'timer-num warn';
    } else {
      fill.style.background = 'var(--green)';
      num.className = 'timer-num';
    }

    if (rem <= 0) clearTimer();
  }
  tick();
  timerLoop = setInterval(tick, 500);
}

function clearTimer() {
  if (timerLoop) { clearInterval(timerLoop); timerLoop = null; }
}

// ── Results ───────────────────────────────────────────────────────────────────
function showResults(leaderboard) {
  show('screen-results');
  const me = leaderboard.find(e => e.student_id === studentId);
  $('r-rank').textContent    = me ? `#${me.rank}` : '—';
  $('r-score').textContent   = me ? Number(me.score || 0).toFixed(1) : '—';
  $('r-correct').textContent = me ? (me.correct_count ?? '—') : '—';

  const lb = $('r-lb');
  lb.innerHTML = '';
  const medals = ['🥇','🥈','🥉'];
  leaderboard.slice(0, 10).forEach(e => {
    const row = document.createElement('div');
    const isMe = e.student_id === studentId;
    row.className = `res-lb-row${isMe ? ' me' : ''}`;
    row.innerHTML = `
      <span class="res-lb-rank">${medals[e.rank - 1] || '#' + e.rank}</span>
      <span class="res-lb-name">${e.name}</span>
      <span class="res-lb-score">${Number(e.score || 0).toFixed(1)}</span>`;
    lb.appendChild(row);
  });

  localStorage.removeItem('exam_sid');
  localStorage.removeItem('exam_sess');
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function toast(msg, type = 'info') {
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.textContent = msg;
  $('toasts').appendChild(t);
  setTimeout(() => t.remove(), 3500);
}

// ── Tab Switching Violation Tracking ──────────────────────────────────────────
document.addEventListener('visibilitychange', () => {
  if (document.hidden && studentId && sessionId && ws?.isConnected) {
    ws.send({
      type: 'violation',
      data: {
        violation_type: 'Tab Switch',
        description: 'Student switched away from the exam tab.'
      }
    });
  }
});

window.addEventListener('blur', () => {
  // We only track blur if it's the active exam screen to prevent false positives
  if (document.getElementById('screen-question').classList.contains('active') && ws?.isConnected) {
    ws.send({
      type: 'violation',
      data: {
        violation_type: 'Window Unfocused',
        description: 'Student lost focus of the exam window.'
      }
    });
  }
});

// ── Auto-restore on reload ────────────────────────────────────────────────────
(async function restore() {
  const sid  = localStorage.getItem('exam_sid');
  const sess = localStorage.getItem('exam_sess');
  const name = localStorage.getItem('exam_name');
  if (sid && sess) {
    studentId = sid; sessionId = sess;
    $('wait-name').textContent = `Welcome back, ${name || 'student'}!`;
    $('wait-code').textContent = 'Reconnecting to session…';
    show('screen-waiting');
    connectWS();
    toast('Reconnecting…', 'info');
  }
})();
