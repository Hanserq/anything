"""
ExamLAN Diagnostic Stress Test
Runs 20 students through 5 questions, tracks every event per student,
reports exactly which students got stuck and at which question.
"""
import asyncio
import json
import random
import time
import httpx
import websockets

API_URL     = "http://127.0.0.1:8000/api"
WS_URL      = "ws://127.0.0.1:8000/ws"
ADMIN_TOKEN = "exam-admin-secret"
NUM_STUDENTS = 20
NUM_QUESTIONS = 5
ANSWER_DELAY  = 0.3   # seconds between receiving Q and answering

QUESTIONS = [
    {
        "text": f"Diagnostic Q{i+1}: what is {i+1}+{i+1}?",
        "options": [str((i+1)*2), str((i+1)*2+1), str((i+1)*2-1), str(i)],
        "correct_index": 0,
        "points": 10,
        "time_limit": 60,
    }
    for i in range(NUM_QUESTIONS)
]

# Per-student state tracking
student_logs: dict[int, list] = {}

def log(idx, msg):
    t = time.strftime("%H:%M:%S")
    entry = f"[{t}] S{idx:03d} {msg}"
    student_logs.setdefault(idx, []).append(entry)
    print(entry)


async def simulate_student(client, idx, session_code):
    log(idx, "joining...")
    try:
        res = await client.post(f"{API_URL}/student/join", json={
            "session_id": session_code,
            "name": f"DiagStudent{idx}",
            "roll_number": f"D{idx:04d}",
        }, timeout=10)
        if res.status_code != 200:
            log(idx, f"JOIN FAILED: {res.text}")
            return
        d = res.json()
        student_id = d["student_id"]
        session_id = d["session_id"]
    except Exception as e:
        log(idx, f"JOIN EXCEPTION: {e}")
        return

    log(idx, f"joined sid={student_id[:8]}")

    url = f"{WS_URL}/student/{session_id}?student_id={student_id}"
    try:
        async with websockets.connect(url, ping_interval=None, open_timeout=10) as ws:
            log(idx, "WS connected")
            questions_answered = 0
            last_event_time = time.time()

            # Watchdog: if no message in 15s after answering, report stuck
            async def watchdog():
                nonlocal last_event_time
                while True:
                    await asyncio.sleep(3)
                    elapsed = time.time() - last_event_time
                    if elapsed > 15:
                        log(idx, f"🔴 STUCK — no message for {elapsed:.1f}s after Q{questions_answered}")
                        return

            dog = asyncio.create_task(watchdog())

            try:
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        log(idx, f"🔴 TIMEOUT waiting for message (answered {questions_answered}/{NUM_QUESTIONS})")
                        break

                    last_event_time = time.time()
                    msg = json.loads(raw)
                    mtype = msg.get("type")
                    data  = msg.get("data", {})

                    if mtype == "connected":
                        log(idx, f"got 'connected' session_status={data.get('session_status')} has_q={bool(data.get('current_question'))}")
                        if data.get("session_status") == "active" and data.get("current_question"):
                            q = data["current_question"]
                            await asyncio.sleep(ANSWER_DELAY)
                            await ws.send(json.dumps({
                                "type": "submit_answer",
                                "data": {"question_id": q["question_id"], "selected_option": 0, "time_taken": ANSWER_DELAY}
                            }))
                            log(idx, f"answered Q (reconnect) q={q['question_id'][:8]}")
                            questions_answered += 1

                    elif mtype == "session_start":
                        log(idx, "got session_start — waiting for question_push")

                    elif mtype == "question_push":
                        q = data
                        log(idx, f"got question_push Q{q['index']+1}/{q['total']} q={q['question_id'][:8]}")
                        await asyncio.sleep(ANSWER_DELAY)
                        await ws.send(json.dumps({
                            "type": "submit_answer",
                            "data": {"question_id": q["question_id"], "selected_option": 0, "time_taken": ANSWER_DELAY}
                        }))
                        log(idx, f"submitted answer for Q{q['index']+1}")
                        questions_answered += 1

                    elif mtype == "answer_ack":
                        log(idx, f"got answer_ack correct={data.get('is_correct')} — waiting for question_push")

                    elif mtype == "exam_end":
                        log(idx, f"✅ DONE — answered {questions_answered}/{NUM_QUESTIONS} questions")
                        break

                    elif mtype == "heartbeat_ack":
                        pass  # ignore

                    else:
                        log(idx, f"got event: {mtype}")

            finally:
                dog.cancel()

    except Exception as e:
        log(idx, f"WS EXCEPTION: {type(e).__name__}: {e}")


async def main():
    print("=" * 60)
    print(f"ExamLAN Diagnostic — {NUM_STUDENTS} students, {NUM_QUESTIONS} questions")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=30) as client:
        # Create session
        res = await client.post(f"{API_URL}/admin/sessions", json={
            "title": f"Diag-{int(time.time())}",
            "session_code": f"DIAG{random.randint(100,999)}",
            "admin_token": ADMIN_TOKEN,
            "per_question_time": 60,
            "pacing_mode": "auto",
            "questions": QUESTIONS,
        })
        sess = res.json()
        sid  = sess["session_id"]
        code = sess["session_code"]
        print(f"Session: {sid} code={code}")

        # Spawn students
        print(f"Spawning {NUM_STUDENTS} students...")
        tasks = [asyncio.create_task(simulate_student(client, i, code)) for i in range(NUM_STUDENTS)]

        # Wait for connections
        await asyncio.sleep(3)

        # Start exam
        print("Starting exam...")
        await client.post(f"{API_URL}/admin/sessions/{sid}/start?token={ADMIN_TOKEN}")
        print("Exam started. Waiting for completions...")

        # Wait — max time = NUM_QUESTIONS * (ANSWER_DELAY + push_delay + buffer) + margin
        max_wait = NUM_QUESTIONS * (ANSWER_DELAY + 1.5) + 30
        await asyncio.sleep(max_wait)

        print("Ending exam...")
        await client.post(f"{API_URL}/admin/sessions/{sid}/end?token={ADMIN_TOKEN}")
        await asyncio.gather(*tasks, return_exceptions=True)

    # Summary
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    stuck = []
    done  = []
    for idx in range(NUM_STUDENTS):
        logs = student_logs.get(idx, [])
        finished = any("✅ DONE" in l for l in logs)
        stuck_msg = next((l for l in logs if "STUCK" in l or "TIMEOUT" in l or "EXCEPTION" in l), None)
        if finished:
            done.append(idx)
        else:
            stuck.append((idx, stuck_msg or logs[-1] if logs else "no logs"))

    print(f"✅ Completed: {len(done)}/{NUM_STUDENTS}")
    print(f"🔴 Stuck/Failed: {len(stuck)}/{NUM_STUDENTS}")
    if stuck:
        print("\nStuck students detail:")
        for idx, reason in stuck[:10]:
            print(f"  S{idx:03d}: {reason}")

    # Print full log for first stuck student if any
    if stuck:
        first_stuck = stuck[0][0]
        print(f"\nFull log for S{first_stuck:03d} (first stuck):")
        for line in student_logs.get(first_stuck, []):
            print(" ", line)


if __name__ == "__main__":
    asyncio.run(main())
