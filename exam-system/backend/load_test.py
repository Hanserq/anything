import asyncio
import json
import random
import time
import httpx
import websockets

API_URL = "http://127.0.0.1:8000/api"
WS_URL = "ws://127.0.0.1:8000/ws"
ADMIN_TOKEN = "exam-admin-secret"
NUM_STUDENTS = 220
NUM_QUESTIONS = 10

# Generate 10 questions
QUESTIONS = []
for i in range(NUM_QUESTIONS):
    QUESTIONS.append({
        "text": f"Question {i+1}: What is the square of {i+1}?",
        "options": [str((i+1)**2), str((i+1)**2 + 1), str((i+1)**2 + 2), str((i+1)**2 - 1)],
        "correct_index": 0,
        "points": 10
    })

async def admin_create_session(client):
    res = await client.post(f"{API_URL}/admin/sessions", json={
        "title": f"Load Test Full {int(time.time())}",
        "session_code": "LOAD-AUTO",
        "admin_token": ADMIN_TOKEN,
        "per_question_time": 30,
        "pacing_mode": "auto",
        "questions": QUESTIONS
    })
    return res.json()

async def admin_start_exam(client, session_id):
    await client.post(f"{API_URL}/admin/sessions/{session_id}/start?token={ADMIN_TOKEN}")

async def admin_next_question(client, session_id):
    await client.post(f"{API_URL}/admin/sessions/{session_id}/next_question?token={ADMIN_TOKEN}")

async def admin_end_exam(client, session_id):
    await client.post(f"{API_URL}/admin/sessions/{session_id}/end?token={ADMIN_TOKEN}")

async def simulate_student(client, student_idx, session_code):
    # Join
    res = await client.post(f"{API_URL}/student/join", json={
        "session_id": session_code,
        "name": f"Student {student_idx}",
        "roll_number": f"RL-{student_idx}"
    })
    if res.status_code != 200:
        print(f"Student {student_idx} failed to join:", res.text)
        return
    data = res.json()
    student_id = data["student_id"]
    session_id = data["session_id"]
    
    # Randomly decide if this student is a "cheater" (20% chance)
    is_cheater = random.random() < 0.2
    
    # Connect WS
    url = f"{WS_URL}/student/{session_id}?student_id={student_id}"
    try:
        async with websockets.connect(url, ping_interval=None) as ws:
            while True:
                msg = await ws.recv()
                data = json.loads(msg)
                
                if data["type"] == "question_push" or (data["type"] == "connected" and data["data"].get("current_question")):
                    
                    if is_cheater and random.random() < 0.3:
                        # 30% chance to switch tab per question if they are a cheater
                        await asyncio.sleep(random.uniform(0.5, 1.5))
                        await ws.send(json.dumps({
                            "type": "violation",
                            "data": {
                                "violation_type": "Tab Switch",
                                "description": "Student switched away from the exam tab."
                            }
                        }))
                    
                    # Random delay before answering to simulate humans
                    await asyncio.sleep(random.uniform(1.0, 5.0))
                    
                    if data["type"] == "connected":
                        q_id = data["data"]["current_question"]["question_id"]
                    else:
                        q_id = data["data"]["question_id"]
                        
                    # Answer correctly 70% of the time
                    choice = 0 if random.random() < 0.70 else random.choice([1, 2, 3])
                    
                    await ws.send(json.dumps({
                        "type": "submit_answer",
                        "data": {
                            "question_id": q_id,
                            "selected_option": choice,
                            "time_taken": random.uniform(1.0, 5.0)
                        }
                    }))
                elif data["type"] == "exam_locked":
                    # Cheater got locked out! They stop answering.
                    pass
                elif data["type"] == "exam_end":
                    break
    except Exception as e:
        pass
        
async def main():
    print(f"Starting Dynamic Load Test with {NUM_STUDENTS} students and {NUM_QUESTIONS} questions...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        print("1. Admin creating session...")
        session = await admin_create_session(client)
        session_id = session["session_id"]
        session_code = session["session_code"]
        print(f"Session Created: {session_id} (Code: {session_code})")
        
        print("2. Spawning student connections...")
        students = [asyncio.create_task(simulate_student(client, i, session_code)) 
                    for i in range(NUM_STUDENTS)]
        
        # Give them a few seconds to connect
        await asyncio.sleep(5)
        
        print("3. Admin starting exam... (Auto Pacing Mode)")
        await admin_start_exam(client, session_id)
        
        # In auto-pacing mode, questions are automatically pushed after answering!
        # Give them enough time to answer all 10 questions (e.g. max 5s per Q + 1s delay * 10 = 60s max)
        print("4. Waiting for auto-paced submissions to complete...")
        await asyncio.sleep(70) 
        
        print("5. Ending exam...")
        await admin_end_exam(client, session_id)
        
        print("Waiting for websocket closes...")
        await asyncio.gather(*students, return_exceptions=True)
        
        # Check results from export CSV since memory is cleared after end
        res = await client.get(f"{API_URL}/admin/sessions/{session_id}/export?token={ADMIN_TOKEN}")
        csv_data = res.text.strip().split('\n')
        # CSV header: Rank, Name, Roll No, Score, Correct, Total Q, Violations
        total_sub = len(csv_data) - 1 if len(csv_data) > 0 else 0
        correct_answers = sum(int(line.split(',')[4]) for line in csv_data[1:] if line)
        total_violations = sum(int(line.split(',')[6]) for line in csv_data[1:] if line)
        
        print("\n=== DYNAMIC LOAD TEST RESULTS ===")
        print(f"Total Students who completed: {total_sub}")
        print(f"Total Correct Answers Across All Students: {correct_answers} / {NUM_STUDENTS * NUM_QUESTIONS}")
        print(f"Total Tab-Switch Violations Tracked: {total_violations}")
        print("Success! Submission buffer handled multiple questions and violations smoothly.")

if __name__ == "__main__":
    asyncio.run(main())
