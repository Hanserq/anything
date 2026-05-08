import time
import logging
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, or_

from database import get_db
from models import Session, Student, Violation
from schemas import StudentJoin
from session_manager import session_manager
from ws_manager import ws_manager
from buffer import submission_buffer
from limiter import limiter

logger = logging.getLogger(__name__)
router = APIRouter(tags=["student"])


def schedule_student_timer(session_id: str, student_id: str, question_id: str, time_limit: int):
    """Schedules an auto-submission if the student does not answer within the time limit."""
    if time_limit <= 0: return

    async def _timer_task():
        import asyncio
        await asyncio.sleep(time_limit + 1) # Grace period
        
        state = session_manager.get_session(session_id)
        if not state or state["status"] != "active": return
        student = state["students"].get(student_id)
        if not student or question_id in student.answered_ids: return
        
        # If student hasn't answered, force submit with -1
        from database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            await _handle_submit(session_id, student_id, {
                "question_id": question_id,
                "selected_option": -1,
                "time_taken": time_limit
            }, db)

    import asyncio
    asyncio.create_task(_timer_task())


# ── Student join (REST) ───────────────────────────────────────────────────────

@router.post("/api/student/join")
@limiter.limit("10/minute")
async def student_join(request: Request, body: StudentJoin, db: AsyncSession = Depends(get_db)):
    # Look up session by code (case-insensitive) OR by UUID
    code_upper = body.session_id.strip().upper()
    result = await db.execute(
        select(Session).where(
            or_(Session.id == body.session_id,
                Session.session_code == code_upper)
        )
    )
    sess = result.scalar_one_or_none()
    if not sess:
        raise HTTPException(404, "Session not found — check your join code")
    if sess.status == "ended":
        raise HTTPException(400, "This session has already ended")

    # Check whitelist
    if sess.allowed_roll_numbers is not None and len(sess.allowed_roll_numbers) > 0:
        if body.roll_number not in sess.allowed_roll_numbers:
            raise HTTPException(403, "Your roll number is not authorized for this exam.")

    # Check duplicate roll number
    existing = await db.execute(
        select(Student).where(
            Student.session_id == sess.id,
            Student.roll_number == body.roll_number,
        )
    )
    ex = existing.scalar_one_or_none()
    if ex:
        # Return existing student (reconnect scenario)
        return {
            "student_id": ex.id,
            "session_id": sess.id,
            "name": ex.name,
            "roll_number": ex.roll_number,
            "session_title": sess.title,
            "session_status": sess.status,
            "reconnect": True,
        }

    student = Student(
        session_id=sess.id,          # Always use the UUID, not the code
        name=body.name,
        roll_number=body.roll_number,
        status="joined",
    )
    db.add(student)
    await db.commit()

    # Register in live state if session already active
    session_manager.add_student(sess.id, student.id, body.name, body.roll_number)

    return {
        "student_id": student.id,
        "session_id": sess.id,
        "name": student.name,
        "roll_number": student.roll_number,
        "session_title": sess.title,
        "session_status": sess.status,
        "reconnect": False,
    }


# ── Student WebSocket ─────────────────────────────────────────────────────────

@router.websocket("/ws/student/{session_id}")
async def student_ws(session_id: str, ws: WebSocket,
                     db: AsyncSession = Depends(get_db)):
    student_id = ws.query_params.get("student_id")
    if not student_id:
        await ws.close(code=4001)
        return

    db_student = await db.get(Student, student_id)
    if not db_student or db_student.session_id != session_id:
        await ws.close(code=4002)
        return

    mem_student = session_manager.get_student(session_id, student_id)
    if db_student.status == "locked" or (mem_student and mem_student.status == "locked"):
        await ws.close(code=4004, reason="Student is locked")
        return

    await ws_manager.connect_student(session_id, student_id, ws)

    # Ensure in-memory student exists
    mem_student = session_manager.get_student(session_id, student_id)
    if not mem_student:
        session_manager.add_student(session_id, student_id,
                                    db_student.name, db_student.roll_number)
        mem_student = session_manager.get_student(session_id, student_id)
        if mem_student:
            mem_student.score = db_student.score
            mem_student.correct_count = db_student.correct_count
            mem_student.strike_count = db_student.strike_count
            mem_student.status = db_student.status

    # Update status to active
    if mem_student:
        mem_student.status = "active"
    await db.execute(update(Student).where(Student.id == student_id)
                     .values(status="active"))
    await db.commit()

    # Send current state on connect
    state = session_manager.get_session(session_id)
    current_q = session_manager.get_student_question(session_id, student_id)
    reconnect_data: dict = {
        "type": "connected",
        "data": {
            "student_id": student_id,
            "name": db_student.name,
            "session_status": state["status"] if state else "waiting",
            "score": db_student.score,
            "strike_count": db_student.strike_count,
            "server_time": time.time(),
        }
    }
    if current_q and state and state["status"] == "active":
        elapsed = time.time() - getattr(current_q, "start_time", time.time())
        reconnect_data["data"]["current_question"] = {
            "question_id": current_q.question_id,
            "index": current_q.index,
            "total": len(state["questions"]),
            "text": current_q.text,
            "options": current_q.options,
            "time_limit": current_q.time_limit,
            "elapsed": elapsed,
            "start_time": current_q.start_time,
            "question_type": current_q.question_type,
            "already_answered": current_q.question_id in (mem_student.answered_ids if mem_student else set()),
        }
    await ws.send_json(reconnect_data)

    # Notify admin
    await ws_manager.broadcast_admins(session_id, {
        "type": "student_connected",
        "data": {"student_id": student_id, "name": db_student.name,
                 "connected_count": ws_manager.connected_count(session_id)}
    })

    try:
        while True:
            msg = await ws.receive_json()
            msg_type = msg.get("type")
            data = msg.get("data", {})

            if msg_type == "submit_answer":
                await _handle_submit(session_id, student_id, data, db)

            elif msg_type == "violation":
                await _handle_violation(session_id, student_id, data, db)

            elif msg_type == "heartbeat":
                await ws_manager.send_to_student(session_id, student_id,
                    {"type": "heartbeat_ack", "server_time": time.time()})

            elif msg_type == "sync_cached":
                # Student sending cached answers after reconnect
                for cached in data.get("answers", []):
                    await _handle_submit(session_id, student_id, cached, db,
                                         is_cached=True)

    except WebSocketDisconnect:
        await ws_manager.disconnect_student(session_id, student_id)
        mem_st = session_manager.get_student(session_id, student_id)
        if mem_st and mem_st.status not in ("locked", "submitted"):
            mem_st.status = "disconnected"
        await db.execute(update(Student).where(Student.id == student_id)
                         .values(status="disconnected"))
        await db.commit()
        await ws_manager.broadcast_admins(session_id, {
            "type": "student_disconnected",
            "data": {"student_id": student_id, "name": db_student.name,
                     "connected_count": ws_manager.connected_count(session_id)}
        })


async def _handle_submit(session_id: str, student_id: str, data: dict,
                          db: AsyncSession, is_cached: bool = False):
    question_id = data.get("question_id")
    selected = data.get("selected_option")
    time_taken = float(data.get("time_taken", 0))

    result = session_manager.record_answer(
        session_id, student_id, question_id, selected, time_taken
    )
    if result is None:
        return   # duplicate or invalid

    result["is_cached"] = is_cached
    accepted = await submission_buffer.add(result)
    if not accepted:
        return

    # Ack to student
    await ws_manager.send_to_student(session_id, student_id, {
        "type": "answer_ack",
        "data": {
            "question_id": question_id,
            "is_correct": result["is_correct"],
            "score_awarded": result["score_awarded"],
            "correct_index": result["correct_shuffled"],
        }
    })

    # Notify admin of live stats + student's new score
    state = session_manager.get_session(session_id)
    answering_student = None
    if state:
        answered = sum(
            1 for st in state["students"].values()
            if question_id in st.answered_ids
        )
        answering_student = state["students"].get(student_id)
        await ws_manager.broadcast_admins(session_id, {
            "type": "answer_stat",
            "data": {
                "question_id": question_id,
                "answered_count": answered,
                "total_students": len(state["students"]),
                "student_id": student_id,
                "score": answering_student.score if answering_student else 0,
            }
        })

    # Auto pacing: schedule the next question push for this individual student
    if state and answering_student and state["config"].get("pacing_mode") == "auto":
        import asyncio
        # Capture stable references so the async closure is safe
        _session_id  = session_id
        _student_id  = student_id
        _total       = len(state["questions"])

        async def push_next():
            await asyncio.sleep(0.6)
            nxt_q = session_manager.get_student_question(_session_id, _student_id)
            if nxt_q:
                # Update server-side tracking
                mem_st = session_manager.get_student(_session_id, _student_id)
                if mem_st:
                    mem_st.current_question_start_time = time.time()
                    
                await ws_manager.send_to_student(_session_id, _student_id, {
                    "type": "question_push",
                    "data": {
                        "question_id": nxt_q.question_id,
                        "index": nxt_q.index,
                        "total": _total,
                        "text": nxt_q.text,
                        "options": nxt_q.options,
                        "time_limit": nxt_q.time_limit,
                        "start_time": time.time(),
                        "question_type": nxt_q.question_type,
                        "pacing_mode": "auto",
                    }
                })
                schedule_student_timer(_session_id, _student_id, nxt_q.question_id, nxt_q.time_limit)
            else:
                # Student finished all questions — send final leaderboard
                board = session_manager.compute_leaderboard(_session_id)
                await ws_manager.send_to_student(_session_id, _student_id, {
                    "type": "exam_end",
                    "data": {
                        "message": "You have completed all questions!",
                        "leaderboard": board,
                    }
                })
        asyncio.create_task(push_next())


async def _handle_violation(session_id: str, student_id: str, data: dict,
                              db: AsyncSession):
    vtype = data.get("violation_type", "unknown")
    strikes = session_manager.record_violation(session_id, student_id)

    db_student = await db.get(Student, student_id)
    viol = Violation(
        session_id=session_id, student_id=student_id,
        violation_type=vtype, description=data.get("description", ""),
        strike_number=strikes,
    )
    db.add(viol)
    await db.execute(update(Student).where(Student.id == student_id)
                     .values(strike_count=strikes))
    await db.commit()

    state = session_manager.get_session(session_id)
    max_strikes = state["config"].get("max_strikes", 3) if state else 3

    await ws_manager.broadcast_admins(session_id, {
        "type": "violation_alert",
        "data": {
            "student_id": student_id,
            "student_name": db_student.name if db_student else "?",
            "roll_number": db_student.roll_number if db_student else "?",
            "violation_type": vtype,
            "strike_count": strikes,
            "max_strikes": max_strikes,
            "occurred_at": datetime.utcnow().isoformat(),
        }
    })

    mem_student = session_manager.get_student(session_id, student_id)
    if mem_student and mem_student.status == "locked":
        await ws_manager.send_to_student(session_id, student_id, {
            "type": "exam_locked",
            "data": {"strikes": strikes,
                     "message": "Exam locked due to violations. Contact admin."}
        })
        await db.execute(update(Student).where(Student.id == student_id)
                         .values(status="locked"))
        await db.commit()


# ── Admin WebSocket ───────────────────────────────────────────────────────────

@router.websocket("/ws/admin/{session_id}")
async def admin_ws(session_id: str, ws: WebSocket):
    token = ws.query_params.get("token")
    from routers.admin import ADMIN_TOKEN
    if token != ADMIN_TOKEN:
        await ws.close(code=4003)
        return

    await ws_manager.connect_admin(session_id, ws)
    try:
        while True:
            msg = await ws.receive_json()
            # Admin can send heartbeat or request leaderboard
            if msg.get("type") == "request_leaderboard":
                state = session_manager.get_session(session_id)
                if state:
                    board = session_manager.compute_leaderboard(session_id)
                    await ws.send_json({
                        "type": "leaderboard_update",
                        "version": state["leaderboard_version"],
                        "data": board,
                    })
            elif msg.get("type") == "admin_broadcast":
                text = msg.get("data", {}).get("message", "")
                if text:
                    await ws_manager.broadcast_students(session_id, {
                        "type": "admin_announcement",
                        "data": {"message": text}
                    })
    except WebSocketDisconnect:
        ws_manager.disconnect_admin(session_id, ws)
