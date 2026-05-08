"""
ExamLAN — 200+ Student Dynamic Stress Test
Tests: individual pacing, tab switches, varied answer times, full flow
"""
import asyncio
import json
import random
import time
import httpx
import websockets
from dataclasses import dataclass, field
from typing import List, Optional

API_URL     = "http://127.0.0.1:8000/api"
WS_URL      = "ws://127.0.0.1:8000/ws"
ADMIN_TOKEN = "exam-admin-secret"
NUM_STUDENTS = 220
NUM_QUESTIONS = 10

QUESTIONS = [
    {
        "text": f"Q{i+1}: What is {i+1} × {i+2}?",
        "options": [str((i+1)*(i+2)), str((i+1)*(i+2)+1), str((i+1)*(i+2)-1), str(i*i)],
        "correct_index": 0,
        "points": 10,
        "time_limit": 45,
    }
    for i in range(NUM_QUESTIONS)
]

@dataclass
class StudentResult:
    idx: int
    completed: bool = False
    questions_received: int = 0
    questions_answered: int = 0
    tab_switches: int = 0
    got_exam_end: bool = False
    error: Optional[str] = None
    duration_s: float = 0.0

results: List[StudentResult] = [StudentResult(idx=i) for i in range(NUM_STUDENTS)]

async def simulate_student(client, idx: int, session_code: str):
    r = results[idx]
    t0 = time.time()
    try:
        res = await client.post(f"{API_URL}/student/join", json={
            "session_id": session_code,
            "name": f"LoadStudent{idx:04d}",
            "roll_number": f"LS{idx:04d}",
        }, timeout=15)
        if res.status_code != 200:
            r.error = f"join_fail:{res.status_code}"; return
        d = res.json()
        student_id = d["student_id"]
        session_id = d["session_id"]
    except Exception as e:
        r.error = f"join_exc:{e}"; return

    url = f"{WS_URL}/student/{session_id}?student_id={student_id}"
    # Each student has a random "persona"
    fast       = random.random() < 0.3   # 30% fast answerers (0.3-1s)
    slow       = random.random() < 0.2   # 20% slow (3-8s)
    cheater    = random.random() < 0.15  # 15% tab-switchers
    accurate   = random.random() < 0.7   # 70% correct answers

    try:
        async with websockets.connect(url, ping_interval=None, open_timeout=15) as ws:
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=60)
                except asyncio.TimeoutError:
                    r.error = f"ws_timeout_after_q{r.questions_answered}"; break

                msg  = json.loads(raw)
                mtype = msg.get("type")
                data  = msg.get("data", {})

                if mtype in ("session_start", "connected", "answer_ack", "pause", "resume"):
                    continue   # no action needed

                if mtype == "question_push":
                    r.questions_received += 1

                    # Tab switch occasionally
                    if cheater and random.random() < 0.25:
                        r.tab_switches += 1
                        await ws.send(json.dumps({
                            "type": "violation",
                            "data": {"violation_type": "Tab Switch", "description": "load-test tab switch"}
                        }))

                    # Answer delay
                    if fast:
                        delay = random.uniform(0.2, 1.0)
                    elif slow:
                        delay = random.uniform(3.0, 8.0)
                    else:
                        delay = random.uniform(0.8, 3.5)
                    await asyncio.sleep(delay)

                    choice = 0 if accurate else random.randint(0, 3)
                    await ws.send(json.dumps({
                        "type": "submit_answer",
                        "data": {
                            "question_id": data["question_id"],
                            "selected_option": choice,
                            "time_taken": delay,
                        }
                    }))
                    r.questions_answered += 1

                elif mtype == "exam_end":
                    r.got_exam_end = True
                    r.completed = True
                    break

                elif mtype == "exam_locked":
                    r.error = "locked"
                    break

    except Exception as e:
        if not r.completed:
            r.error = f"ws_exc:{type(e).__name__}"

    r.duration_s = time.time() - t0


async def main():
    print("=" * 65)
    print(f"ExamLAN 200+ Load Test — {NUM_STUDENTS} students, {NUM_QUESTIONS} questions")
    print("=" * 65)

    t_total = time.time()

    async with httpx.AsyncClient(timeout=30) as client:
        # Create session
        code = f"LOAD{random.randint(1000,9999)}"
        res = await client.post(f"{API_URL}/admin/sessions", json={
            "title": f"LoadTest-{NUM_STUDENTS}",
            "session_code": code,
            "admin_token": ADMIN_TOKEN,
            "per_question_time": 45,
            "pacing_mode": "auto",
            "questions": QUESTIONS,
        })
        sess = res.json()
        sid  = sess["session_id"]
        print(f"✅ Session created: {sid[:8]}… code={code}")

        # Spawn all students
        print(f"🚀 Spawning {NUM_STUDENTS} student coroutines…")
        tasks = [asyncio.create_task(simulate_student(client, i, code)) for i in range(NUM_STUDENTS)]

        # Wait for connection wave
        await asyncio.sleep(4)

        # Start exam
        print("▶  Starting exam…")
        t_start = time.time()
        await client.post(f"{API_URL}/admin/sessions/{sid}/start?token={ADMIN_TOKEN}")
        print("   Exam started — monitoring…")

        # Monitor completion every 5s
        for tick in range(60):   # max 5 min
            await asyncio.sleep(5)
            done = sum(1 for r in results if r.completed)
            errs = sum(1 for r in results if r.error)
            print(f"   t+{(tick+1)*5:3d}s | completed={done} | errors={errs} | in-progress={NUM_STUDENTS-done-errs}")
            if done + errs >= NUM_STUDENTS:
                break

        print("⏹  Ending exam…")
        await client.post(f"{API_URL}/admin/sessions/{sid}/end?token={ADMIN_TOKEN}")

        # Wait for remaining tasks
        await asyncio.gather(*tasks, return_exceptions=True)

    # ── Summary ──────────────────────────────────────────────────────
    elapsed = time.time() - t_total

    completed  = [r for r in results if r.completed]
    stuck      = [r for r in results if not r.completed and not r.error]
    errors     = [r for r in results if r.error]
    tab_total  = sum(r.tab_switches for r in results)
    avg_dur    = sum(r.duration_s for r in completed) / len(completed) if completed else 0
    fully_done = [r for r in completed if r.questions_answered == NUM_QUESTIONS]

    print("\n" + "=" * 65)
    print("LOAD TEST RESULTS")
    print("=" * 65)
    print(f"  Total students      : {NUM_STUDENTS}")
    print(f"  ✅ Completed        : {len(completed)} ({len(completed)/NUM_STUDENTS*100:.1f}%)")
    print(f"  ✅ All Qs answered  : {len(fully_done)} ({len(fully_done)/NUM_STUDENTS*100:.1f}%)")
    print(f"  ⚠️  Stuck (no end)  : {len(stuck)}")
    print(f"  ❌ Errors           : {len(errors)}")
    print(f"  ⏱  Avg duration     : {avg_dur:.1f}s per student")
    print(f"  🔄 Tab switches     : {tab_total}")
    print(f"  ⏱  Wall clock       : {elapsed:.1f}s")
    print(f"  📦 Throughput       : {len(completed)*NUM_QUESTIONS/elapsed:.1f} answers/sec")

    if errors:
        from collections import Counter
        err_types = Counter(r.error.split(":")[0] for r in errors)
        print(f"\n  Error breakdown: {dict(err_types)}")

    if stuck:
        print(f"\n  First stuck student: answered {stuck[0].questions_answered}/{NUM_QUESTIONS}, received {stuck[0].questions_received}")

    print("\n" + "=" * 65)
    if len(fully_done) / NUM_STUDENTS >= 0.98:
        print("🎉 PASS — ≥98% of students completed all questions")
    else:
        print(f"❌ FAIL — only {len(fully_done)/NUM_STUDENTS*100:.1f}% completed")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())
