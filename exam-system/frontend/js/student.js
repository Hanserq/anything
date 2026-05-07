// Student portal — updated for session_code, mobile drawer leaderboard
const API = window.location.origin;
const WS_BASE = `ws://${window.location.host}`;

let studentId  = null;
let sessionId  = null;   // this is the DB UUID, resolved after join
let wsClient   = null;
let antiCheat  = null;
let currentQ   = null;
let timerTick  = null;

const $ = id => document.getElementById(id);

function showScreen(name) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.querySelector(`.screen[data-screen="${name}"]`)?.classList.add('active');
}

function setConn(ok) {
  const pill = $('conn-pill');
  $('conn-label').textContent = ok ? 'Connected' : 'Reconnecting…';
  pill.classList.toggle('connected', ok);
}

// ── Join ───────────────────────────────────────────────────────────────────────
$('join-btn').addEventListener('click', handleJoin);
$('inp-code').addEventListener('keydown', e => { if (e.key === 'Enter') handleJoin(); });

async function handleJoin() {
  const name = $('inp-name').value.trim();
  const roll = $('inp-roll').value.trim();
  const code = $('inp-code').value.trim();
  if (!name) { showToast('Enter your name', 'error'); $('inp-name').focus(); return; }
  if (!roll) { showToast('Enter your roll number', 'error'); $('inp-roll').focus(); return; }
  if (!code) { showToast('Enter the join code', 'error'); $('inp-code').focus(); return; }

  $('join-btn').disabled = true;
  $('join-btn').textContent = 'Joining…';

  try {
    const res = await fetch(`${API}/api/student/join`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: code, name, roll_number: roll }),
    });
    if (!res.ok) {
      const e = await res.json();
      showToast(e.detail || 'Join failed', 'error');
      return;
    }
    const data = await res.json();
    studentId = data.student_id;
    sessionId = data.session_id;   // server returns resolved UUID

    // Persist for reconnect
    localStorage.setItem('exam_sid', studentId);
    localStorage.setItem('exam_sess', sessionId);
    localStorage.setItem('exam_name', data.name);

    await ExamDB.open();
    await ExamDB.saveState('student_id', studentId);
    await ExamDB.saveState('session_id', sessionId);

    $('waiting-name').textContent = data.name;
    $('session-label').textContent = `Session: ${data.session_title || code}`;
    showScreen('waiting');
    connectWS();
  } catch (err) {
    showToast('Server unreachable — check your Wi-Fi connection', 'error');
  } finally {
    $('join-btn').disabled = false;
    $('join-btn').textContent = 'Join Exam →';
  }
}

// ── WebSocket ──────────────────────────────────────────────────────────────────
function connectWS() {
  const url = `${WS_BASE}/ws/student/${sessionId}?student_id=${studentId}`;
  wsClient = new ExamWSClient({
    url,
    onOpen: handleWSOpen,
    onMessage: handleWSMsg,
    onClose: () => setConn(false),
    onError: () => setConn(false),
  });
  wsClient.connect();
}

async function handleWSOpen() {
  setConn(true);
  // Sync cached offline answers
  const pending = await ExamDB.getPendingAnswers();
  if (pending.length) {
    wsClient.send({ type: 'sync_cached', data: { answers: pending } });
    await ExamDB.clearPendingAnswers();
  }
}

function handleWSMsg(msg) {
  const { type, data } = msg;
  switch (type) {
    case 'connected':
      if (data.session_status === 'active' && data.current_question) {
        renderQuestion(data.current_question, data.current_question.elapsed || 0);
      } else {
        showScreen('waiting');
      }
      if (data.strike_count > 0 && antiCheat) antiCheat.setStrikes(data.strike_count);
      break;
    case 'session_start':
      showToast('Exam started! First question coming…', 'info');
      showScreen('waiting');
      break;
    case 'question_push':
      startAntiCheat();
      renderQuestion(data, 0);
      break;
    case 'question_end':
      clearTimer();
      revealCorrect(data.correct_index, data.correct_text);
      break;
    case 'leaderboard_update':
      renderLeaderboard(msg.data);
      break;
    case 'answer_ack':
      markAnswered(data.question_id, data.is_correct, data.score_awarded);
      break;
    case 'pause':
      showToast('⏸ Exam paused by teacher', 'warning');
      break;
    case 'resume':
      showToast('▶ Exam resumed', 'info');
      break;
    case 'exam_locked':
      lockExam(data.message);
      break;
    case 'exam_unlocked':
      unlockExam();
      break;
    case 'exam_end':
      endExam(msg.data.leaderboard);
      break;
    case 'heartbeat_ack':
      break;
  }
}

// ── Question rendering ─────────────────────────────────────────────────────────
function renderQuestion(q, elapsed = 0) {
  currentQ = q;
  showScreen('question');
  $('q-badge').textContent = `Q${q.index + 1} / ${q.total}`;
  $('q-text').textContent = q.text;

  const grid = $('options-grid');
  grid.innerHTML = '';
  q.options.forEach((opt, i) => {
    const btn = document.createElement('button');
    btn.className = 'opt-btn';
    btn.dataset.index = i;
    btn.innerHTML = `<span class="opt-label">${'ABCDE'[i]}</span><span>${opt}</span>`;
    btn.addEventListener('click', () => submitAnswer(i, btn));
    grid.appendChild(btn);
  });

  // Already answered?
  if (q.already_answered) {
    grid.querySelectorAll('.opt-btn').forEach(b => b.disabled = true);
  }

  if (q.time_limit > 0) {
    startTimer(q.time_limit, elapsed, q.start_time);
  } else {
    $('timer-num').textContent = '∞';
    $('timer-fill').style.width = '100%';
  }
}

function submitAnswer(idx, btn) {
  if (!currentQ || document.querySelector('.opt-btn.selected')) return;

  const timeTaken = currentQ.time_limit > 0
    ? Math.min(currentQ.time_limit, Date.now() / 1000 - currentQ.start_time)
    : 0;

  btn.classList.add('selected');
  document.querySelectorAll('.opt-btn').forEach(b => b.disabled = true);

  const payload = {
    type: 'submit_answer',
    data: { question_id: currentQ.question_id, selected_option: idx, time_taken: timeTaken },
  };

  if (wsClient?.isConnected) {
    wsClient.send(payload);
  } else {
    ExamDB.queueAnswer(payload.data);
    showToast('Saved offline — will sync on reconnect', 'warning');
  }
}

function markAnswered(questionId, isCorrect, score) {
  if (!currentQ || currentQ.question_id !== questionId) return;
  const sel = document.querySelector('.opt-btn.selected');
  if (sel) sel.classList.add(isCorrect ? 'correct' : 'wrong');
  showToast(isCorrect ? `✅ Correct! +${score.toFixed(1)} pts` : '❌ Incorrect', isCorrect ? 'success' : 'error');
}

function revealCorrect(correctIdx) {
  document.querySelectorAll('.opt-btn').forEach((btn, i) => {
    btn.disabled = true;
    if (i === correctIdx) btn.classList.add('correct-reveal');
  });
}

// ── Timer ──────────────────────────────────────────────────────────────────────
function startTimer(limit, elapsed, startTime) {
  clearTimer();
  function tick() {
    const rem = Math.max(0, limit - (Date.now() / 1000 - startTime));
    $('timer-num').textContent = Math.ceil(rem);
    const pct = (rem / limit) * 100;
    $('timer-fill').style.width = pct + '%';
    $('timer-fill').style.background =
      rem < 10 ? '#f43f5e' : rem < limit * 0.4 ? '#f59e0b' : '#10b981';
    if (rem <= 0) clearTimer();
  }
  tick();
  timerTick = setInterval(tick, 500);
}
function clearTimer() {
  if (timerTick) { clearInterval(timerTick); timerTick = null; }
}

// ── Leaderboard ────────────────────────────────────────────────────────────────
function renderLeaderboard(entries) {
  [$('lb-desktop'), $('lb-mobile'), ].forEach(el => {
    if (!el) return;
    el.innerHTML = '';
    entries.slice(0, 10).forEach(e => {
      const li = document.createElement('div');
      li.className = `lb-item${e.student_id === studentId ? ' lb-me' : ''}`;
      li.innerHTML = `
        <span class="lb-rank">${['🥇','🥈','🥉'][e.rank-1] || '#'+e.rank}</span>
        <span class="lb-name">${e.name}</span>
        <span class="lb-score">${e.score.toFixed(1)}</span>`;
      el.appendChild(li);
    });
  });
}

// Mobile drawer
$('lb-toggle-btn').addEventListener('click', () => {
  $('lb-drawer').classList.toggle('open');
});
$('lb-drawer').addEventListener('click', e => {
  if (e.target === $('lb-drawer') || e.target.classList.contains('drawer-handle')) {
    $('lb-drawer').classList.remove('open');
  }
});

// ── Anti-cheat ─────────────────────────────────────────────────────────────────
function startAntiCheat() {
  if (antiCheat) return;
  antiCheat = new AntiCheat({
    maxStrikes: 3,
    onViolation: ({ violation_type, description, strike }) => {
      const pill = $('strike-pill');
      pill.textContent = `⚠ Strike ${strike} — ${violation_type.replace('_', ' ')}`;
      pill.classList.add('show');
      setTimeout(() => pill.classList.remove('show'), 3000);
      wsClient?.send({ type: 'violation', data: { violation_type, description } });
    },
    onLocked: strikes => lockExam(`Exam locked after ${strikes} violations. Contact your teacher.`),
  });
  antiCheat.start();
}

function lockExam(msg) {
  antiCheat?.stop(); antiCheat = null;
  clearTimer();
  $('lock-msg').textContent = msg;
  showScreen('locked');
}

function unlockExam() {
  showScreen('question');
  antiCheat = null;
  startAntiCheat();
  if (currentQ) renderQuestion(currentQ);
}

// ── Exam end ───────────────────────────────────────────────────────────────────
function endExam(leaderboard = []) {
  antiCheat?.stop(); antiCheat = null;
  clearTimer();
  showScreen('results');
  const me = leaderboard.find(e => e.student_id === studentId);
  if (me) {
    $('res-rank').textContent = `#${me.rank}`;
    $('res-score').textContent = me.score.toFixed(1);
    $('res-correct').textContent = `${me.correct_count}`;
  }
  renderLeaderboard(leaderboard);
  // Results leaderboard
  const rl = $('lb-results');
  if (rl) {
    rl.innerHTML = '';
    leaderboard.slice(0, 10).forEach(e => {
      const li = document.createElement('div');
      li.className = `lb-item${e.student_id === studentId ? ' lb-me' : ''}`;
      li.innerHTML = `
        <span class="lb-rank">${['🥇','🥈','🥉'][e.rank-1] || '#'+e.rank}</span>
        <span class="lb-name">${e.name}</span>
        <span class="lb-score">${e.score.toFixed(1)}</span>`;
      rl.appendChild(li);
    });
  }
  localStorage.removeItem('exam_sid');
  localStorage.removeItem('exam_sess');
}

// ── Toast ──────────────────────────────────────────────────────────────────────
function showToast(msg, type = 'info') {
  const t = document.createElement('div');
  t.className = `toast toast-${type}`;
  t.textContent = msg;
  $('toast-container').appendChild(t);
  setTimeout(() => t.classList.add('show'), 10);
  setTimeout(() => { t.classList.remove('show'); setTimeout(() => t.remove(), 300); }, 3500);
}

// ── Auto-restore session on page reload ────────────────────────────────────────
(async function restoreSession() {
  await ExamDB.open();
  const sid  = localStorage.getItem('exam_sid');
  const sess = localStorage.getItem('exam_sess');
  const name = localStorage.getItem('exam_name');
  if (sid && sess) {
    studentId = sid;
    sessionId = sess;
    $('waiting-name').textContent = name || 'Student';
    showScreen('waiting');
    connectWS();
    showToast('Reconnecting to your session…', 'info');
  }
})();
