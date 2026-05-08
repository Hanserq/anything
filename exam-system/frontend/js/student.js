// ExamLAN Student Portal — Auto-pacing fixed
'use strict';

const API     = window.location.origin;
const WS_BASE = `ws://${window.location.host}`;

let studentId    = null;
let sessionId    = null;
let ws           = null;
let timerLoop    = null;
let currentQ     = null;
let skipTimer    = null;   // timeout to show the manual skip button

const $ = id => document.getElementById(id);

// ── Custom Confirm Modal Helper ──────────────────────────────────────────────
window.confirmAsync = function(title, msg, isDanger = false) {
  return new Promise((resolve) => {
    const modal = $('confirm-modal');
    const t = $('confirm-title');
    const m = $('confirm-msg');
    const ok = $('confirm-ok');
    const cancel = $('confirm-cancel');
    if (!modal || !ok || !cancel) { console.error('Modal elements missing'); return resolve(false); }

    t.textContent = title;
    m.textContent = msg;
    ok.className = isDanger ? 'modal-btn confirm danger' : 'modal-btn confirm';
    modal.style.display = 'flex';

    const cleanup = (val) => {
      modal.style.display = 'none';
      ok.onclick = null; cancel.onclick = null; modal.onclick = null;
      resolve(val);
    };

    ok.onclick = () => cleanup(true);
    cancel.onclick = () => cleanup(false);
    modal.onclick = (e) => { if (e.target === modal) cleanup(false); };
  });
};

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

// ── Leave session (waiting screen) ────────────────────────────────────────────
$('btn-leave').onclick = async () => {
  if (!(await confirmAsync('Leave Session', 'Are you sure you want to leave? You will need to re-enter the join code.', true))) return;
  if (ws) { ws.close(); ws = null; }
  studentId = null; sessionId = null;
  localStorage.removeItem('exam_sid');
  localStorage.removeItem('exam_sess');
  localStorage.removeItem('exam_name');
  // Reset join form
  ['inp-name','inp-roll','inp-code'].forEach(id => $(id).value = '');
  show('screen-join');
  toast('You have left the session.', 'info');
};

const forceReset = async () => {
  if (!(await confirmAsync('Reset State', 'This will completely reset your browser state and clear any saved session. Continue?', true))) return;
  localStorage.clear();
  window.location.reload();
};
$('btn-reset-join').onclick = forceReset;
$('btn-reset-wait').onclick = forceReset;

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
    sessionId = d.session_id;
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
  try {
    await ExamDB.open();
    const pending = await ExamDB.getPendingAnswers();
    if (pending.length) {
      ws.send({ type: 'sync_cached', data: { answers: pending } });
      await ExamDB.clearPendingAnswers();
    }
  } catch {}
}

// ── Message handler ───────────────────────────────────────────────────────────
function handleMsg(msg) {
  const { type, data } = msg;
  switch (type) {
    case 'connected':
      // Reconnect — if already on a question, resume it
      if (data.session_status === 'active' && data.current_question) {
        renderQuestion(data.current_question, data.current_question.elapsed || 0);
      } else {
        show('screen-waiting');
      }
      break;

    case 'session_start':
      // Server will immediately push Q1 via question_push — just show brief toast
      toast('Exam starting!', 'ok');
      break;

    case 'question_push':
      // A new question for THIS student individually
      clearSkipTimer();
      hideFeedback();
      renderQuestion(data, 0);
      break;

    case 'answer_ack':
      // Server confirmed our answer — show feedback, then await question_push
      handleAck(data);
      break;

    case 'question_end':
      // Global timer expired for the admin-paced question (shouldn't fire in auto mode)
      // We just reveal correct and wait for the server to push next
      clearTimer();
      if (currentQ && data.correct_index !== undefined) {
        revealCorrect(data.correct_index);
      }
      break;

    case 'exam_locked':
      clearTimer(); clearSkipTimer();
      $('lock-msg').textContent = data.message || 'Your exam has been locked.';
      show('screen-locked');
      break;

    case 'exam_unlocked':
      toast('Your exam has been unlocked by the admin.', 'ok');
      show('screen-waiting');
      break;

    case 'pause':
      toast('⏸ Exam paused by teacher', 'info');
      break;

    case 'resume':
      toast('▶ Exam resumed', 'ok');
      break;

    case 'admin_announcement':
      toast('📢 ' + (data.message || 'Announcement'), 'info');
      break;

    case 'exam_end':
      clearTimer(); clearSkipTimer();
      showResults((data || msg.data)?.leaderboard || []);
      break;
  }
}

// ── Question rendering ────────────────────────────────────────────────────────
function renderQuestion(q, elapsed) {
  currentQ = q;
  show('screen-question');
  hideFeedback();
  $('q-skip-row').style.display = 'none';
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

// ── Option selection ──────────────────────────────────────────────────────────
function pickOption(idx, btn) {
  if (document.querySelector('.opt-btn.selected')) return; // already picked

  // Lock all options immediately so student can't double-click
  document.querySelectorAll('.opt-btn').forEach(b => { b.disabled = true; });
  btn.classList.add('selected');
  clearTimer();

  const timeTaken = (currentQ.time_limit > 0 && currentQ.start_time)
    ? Math.min(currentQ.time_limit, Date.now() / 1000 - currentQ.start_time)
    : 0;

  const payload = {
    type: 'submit_answer',
    data: { question_id: currentQ.question_id, selected_option: idx, time_taken: timeTaken },
  };

  if (ws?.isConnected) {
    ws.send(payload);
    // Start a skip timer — if server doesn't respond in 4s show manual skip
    startSkipTimer();
  } else {
    ExamDB.queueAnswer(payload.data).catch(() => {});
    toast('Saved offline — will sync on reconnect', 'info');
    showFeedback(false, 0, null);
  }
}

// ── Answer acknowledgement ────────────────────────────────────────────────────
function handleAck({ is_correct, score_awarded, question_id, correct_index }) {
  // Guard: ignore acks for a question we've already moved past
  if (!currentQ || currentQ.question_id !== question_id) return;

  clearSkipTimer(); // server responded, cancel the stuck-detection timer

  // Show correct/wrong styling on selected option
  const sel = document.querySelector('.opt-btn.selected');
  if (sel) sel.classList.add(is_correct ? 'correct' : 'wrong');

  // Always reveal the correct answer
  revealCorrect(correct_index);

  // Show inline feedback bar
  showFeedback(is_correct, score_awarded, correct_index);
  playChime(is_correct);

  // The server will send a 'question_push' in ~0.6s automatically.
  // We show a brief "loading next…" indicator after 1.5s if push hasn't arrived.
  startSkipTimer(1500);
}

// ── Feedback bar (inline, not an overlay) ────────────────────────────────────
function showFeedback(isCorrect, score, correctIdx) {
  const el = $('q-feedback');
  if (!el) return;
  if (isCorrect) {
    el.textContent = score > 0 ? `✓ Correct! +${Number(score).toFixed(1)} pts` : '✓ Correct!';
    el.style.display = 'block';
    el.style.background = 'rgba(16,185,129,0.15)';
    el.style.border = '1px solid var(--green)';
    el.style.color = 'var(--green)';
  } else {
    el.textContent = '✗ Incorrect';
    el.style.display = 'block';
    el.style.background = 'rgba(239,68,68,0.12)';
    el.style.border = '1px solid var(--red)';
    el.style.color = 'var(--red)';
  }
}

function hideFeedback() {
  const el = $('q-feedback');
  if (el) el.style.display = 'none';
}

// ── Audio ─────────────────────────────────────────────────────────────────────
let audioCtx = null;
function playChime(isCorrect) {
  if (!audioCtx) {
    try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); }
    catch (e) { return; }
  }
  if (audioCtx.state === 'suspended') audioCtx.resume();
  
  const osc = audioCtx.createOscillator();
  const gain = audioCtx.createGain();
  osc.connect(gain);
  gain.connect(audioCtx.destination);
  
  const now = audioCtx.currentTime;
  if (isCorrect) {
    osc.type = 'sine';
    osc.frequency.setValueAtTime(523.25, now); // C5
    osc.frequency.setValueAtTime(659.25, now + 0.1); // E5
    gain.gain.setValueAtTime(0.1, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.5);
    osc.start(now);
    osc.stop(now + 0.5);
  } else {
    osc.type = 'sawtooth';
    osc.frequency.setValueAtTime(200, now);
    osc.frequency.setValueAtTime(150, now + 0.15);
    gain.gain.setValueAtTime(0.1, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.4);
    osc.start(now);
    osc.stop(now + 0.4);
  }
}

// ── Reveal correct option ─────────────────────────────────────────────────────
function revealCorrect(correctIdx) {
  if (correctIdx === undefined || correctIdx === null) return;
  document.querySelectorAll('.opt-btn').forEach((b, i) => {
    b.disabled = true;
    if (i === correctIdx && !b.classList.contains('correct')) {
      b.classList.add('correct-reveal');
    }
  });
}

// ── Skip timer — shows manual button if server is slow / stuck ────────────────
function startSkipTimer(delayMs = 4000) {
  clearSkipTimer();
  skipTimer = setTimeout(() => {
    const skipRow = $('q-skip-row');
    if (skipRow) skipRow.style.display = 'block';
  }, delayMs);
}

function clearSkipTimer() {
  if (skipTimer) { clearTimeout(skipTimer); skipTimer = null; }
  const skipRow = $('q-skip-row');
  if (skipRow) skipRow.style.display = 'none';
}

// Manual skip: ask the server for the current state (triggers reconnect logic)
$('btn-skip').onclick = () => {
  clearSkipTimer();
  $('q-skip-row').style.display = 'none';
  toast('Requesting next question…', 'info');
  // Re-ping server by sending heartbeat — server will reply with current question on reconnect
  if (ws?.isConnected) {
    ws.send({ type: 'heartbeat' });
  } else {
    connectWS();
  }
};

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

    if (rem <= 0) {
      clearTimer();
      // Timer ran out and no answer was given — show skip button so they can manually advance
      if (!document.querySelector('.opt-btn.selected')) {
        document.querySelectorAll('.opt-btn').forEach(b => b.disabled = true);
        showFeedback(false, 0, null);
        const fb = $('q-feedback');
        if (fb) fb.textContent = '⏱ Time\'s up!';
        startSkipTimer(500);
      }
    }
  }
  tick();
  timerLoop = setInterval(tick, 500);
}

function clearTimer() {
  if (timerLoop) { clearInterval(timerLoop); timerLoop = null; }
}

function formatTime(sec) {
  if (!sec) return '';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s < 10 ? '0' : ''}${s}`;
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
      <span class="res-lb-score">${Number(e.score || 0).toFixed(1)}</span>
      <span class="res-lb-time">${formatTime(e.time_taken)}</span>`;
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
