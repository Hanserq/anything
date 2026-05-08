import os
import csv
import io
import json
import asyncio
import logging
import time
import random
import string
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from database import get_db
from models import Session, Student, Question, Violation, Result, Folder
from schemas import SessionCreate, QuestionAddBatch, FolderCreate
from session_manager import session_manager
from ws_manager import ws_manager
from buffer import submission_buffer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "exam-admin-secret")


def verify_admin(token: str):
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid admin token")


# ── Folders ───────────────────────────────────────────────────────────────────

@router.post("/folders")
async def create_folder(body: FolderCreate, token: str = Query(...), db: AsyncSession = Depends(get_db)):
    verify_admin(token)
    folder = Folder(name=body.name, parent_id=body.parent_id)
    db.add(folder)
    await db.commit()
    return {"id": folder.id, "name": folder.name, "parent_id": folder.parent_id}

@router.get("/folders")
async def list_folders(token: str = Query(...), db: AsyncSession = Depends(get_db)):
    verify_admin(token)
    result = await db.execute(select(Folder))
    folders = result.scalars().all()
    return [{"id": f.id, "name": f.name, "parent_id": f.parent_id} for f in folders]

@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str, token: str = Query(...), db: AsyncSession = Depends(get_db)):
    verify_admin(token)
    f = await db.get(Folder, folder_id)
    if not f:
        raise HTTPException(404, "Folder not found")
    await db.delete(f)
    await db.commit()
    return {"status": "deleted"}

@router.put("/folders/{folder_id}")
async def rename_folder(folder_id: str, body: dict, token: str = Query(...), db: AsyncSession = Depends(get_db)):
    verify_admin(token)
    f = await db.get(Folder, folder_id)
    if not f:
        raise HTTPException(404, "Folder not found")
    f.name = body.get("name", f.name)
    await db.commit()
    return {"id": f.id, "name": f.name, "parent_id": f.parent_id}

# ── Session CRUD ──────────────────────────────────────────────────────────────

def _gen_code():
    """Generate a random 6-char uppercase join code."""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))


@router.post("/sessions")
async def create_session(body: SessionCreate, db: AsyncSession = Depends(get_db)):
    verify_admin(body.admin_token)

    # Use custom code or auto-generate
    code = (body.session_code or "").strip().upper().replace(" ", "-") or _gen_code()

    # Check uniqueness
    existing = await db.execute(select(Session).where(Session.session_code == code))
    if existing.scalar_one_or_none():
        code = code + "-" + _gen_code()[:3]  # suffix to make unique

    sess = Session(
        title=body.title,
        session_code=code,
        description=body.description,
        admin_token=ADMIN_TOKEN,
        per_question_time=body.per_question_time,
        time_limit=body.time_limit,
        randomize_questions=body.randomize_questions,
        randomize_options=body.randomize_options,
        max_strikes=body.max_strikes,
        pacing_mode=body.pacing_mode,
        class_name=body.class_name,
        category=body.category,
        folder_id=body.folder_id,
    )
    db.add(sess)
    await db.flush()

    for idx, qdata in enumerate(body.questions):
        q = Question(
            session_id=sess.id,
            index=idx,
            text=qdata.text,
            options=qdata.options,
            correct_index=qdata.correct_index,
            points=qdata.points,
            time_limit=qdata.time_limit if qdata.time_limit else body.per_question_time,
            question_type=qdata.question_type,
        )
        db.add(q)

    await db.commit()
    return {"session_id": sess.id, "session_code": sess.session_code,
            "title": sess.title, "status": sess.status}


@router.get("/sessions")
async def list_sessions(token: str = Query(...), db: AsyncSession = Depends(get_db)):
    verify_admin(token)
    result = await db.execute(select(Session).order_by(Session.created_at.desc()))
    sessions = result.scalars().all()
    out = []
    for s in sessions:
        stud_r = await db.execute(select(Student).where(Student.session_id == s.id))
        q_r = await db.execute(select(Question).where(Question.session_id == s.id))
        out.append({
            "id": s.id, "session_code": s.session_code, "title": s.title,
            "description": s.description, "status": s.status,
            "class_name": s.class_name, "category": s.category,
            "folder_id": s.folder_id,
            "created_at": s.created_at.isoformat(),
            "student_count": len(stud_r.scalars().all()),
            "question_count": len(q_r.scalars().all()),
        })
    return out


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, token: str = Query(...),
                      db: AsyncSession = Depends(get_db)):
    verify_admin(token)
    sess = await db.get(Session, session_id)
    if not sess:
        raise HTTPException(404, "Session not found")

    stud_r = await db.execute(select(Student).where(Student.session_id == session_id))
    students = stud_r.scalars().all()
    q_r = await db.execute(select(Question).where(Question.session_id == session_id)
                           .order_by(Question.index))
    questions = q_r.scalars().all()

    live_state = session_manager.get_session(session_id)

    return {
        "id": sess.id, "session_code": sess.session_code,
        "title": sess.title, "description": sess.description,
        "status": sess.status,
        "class_name": sess.class_name, "category": sess.category,
        "folder_id": sess.folder_id,
        "per_question_time": sess.per_question_time,
        "time_limit": sess.time_limit,
        "randomize_questions": sess.randomize_questions,
        "randomize_options": sess.randomize_options,
        "max_strikes": sess.max_strikes,
        "pacing_mode": sess.pacing_mode,
        "created_at": sess.created_at.isoformat(),
        "started_at": sess.started_at.isoformat() if sess.started_at else None,
        "students": [
            {"id": s.id, "name": s.name, "roll_number": s.roll_number,
             "score": s.score, "status": s.status, "strike_count": s.strike_count}
            for s in students
        ],
        "questions": [
            {"id": q.id, "index": q.index, "text": q.text,
             "options": q.options, "correct_index": q.correct_index,
             "points": q.points, "time_limit": q.time_limit}
            for q in questions
        ],
        "connected_count": ws_manager.connected_count(session_id),
        "leaderboard": live_state["leaderboard"] if live_state else [],
        "current_question_index": live_state.get("current_index", -1) if live_state else -1,
        "total_questions": len(questions),
        "session_status_live": live_state["status"] if live_state else sess.status,
    }


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, token: str = Query(...),
                         db: AsyncSession = Depends(get_db)):
    verify_admin(token)
    sess = await db.get(Session, session_id)
    if not sess:
        raise HTTPException(404, "Session not found")
        
    await db.delete(sess)
    await db.commit()
    
    session_manager.remove_session(session_id)
    return {"status": "deleted"}


@router.post("/sessions/{session_id}/questions")
async def add_questions(session_id: str, body: QuestionAddBatch,
                        db: AsyncSession = Depends(get_db)):
    verify_admin(body.admin_token)
    sess = await db.get(Session, session_id)
    if not sess:
        raise HTTPException(404, "Session not found")
    if sess.status != "waiting":
        raise HTTPException(400, "Can only add questions before session starts")

    q_r = await db.execute(select(Question).where(Question.session_id == session_id))
    existing_count = len(q_r.scalars().all())

    for i, qdata in enumerate(body.questions):
        q = Question(
            session_id=session_id, index=existing_count + i,
            text=qdata.text, options=qdata.options,
            correct_index=qdata.correct_index, points=qdata.points,
            time_limit=qdata.time_limit or sess.per_question_time,
            question_type=qdata.question_type,
        )
        db.add(q)
    await db.commit()
    return {"added": len(body.questions)}


# ── Session control ───────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/start")
async def start_session(session_id: str, token: str = Query(...),
                        db: AsyncSession = Depends(get_db)):
    verify_admin(token)
    sess = await db.get(Session, session_id)
    if not sess:
        raise HTTPException(404)
    if sess.status not in ("waiting", "paused"):
        raise HTTPException(400, f"Cannot start session in status '{sess.status}'")

    q_r = await db.execute(select(Question).where(Question.session_id == session_id)
                           .order_by(Question.index))
    questions = [
        {"id": q.id, "text": q.text, "options": q.options,
         "correct_index": q.correct_index, "points": q.points,
         "time_limit": q.time_limit, "question_type": q.question_type}
        for q in q_r.scalars().all()
    ]
    if not questions:
        raise HTTPException(400, "Session has no questions")

    config = {
        "per_question_time": sess.per_question_time,
        "time_limit": sess.time_limit,
        "randomize_questions": sess.randomize_questions,
        "randomize_options": sess.randomize_options,
        "max_strikes": sess.max_strikes,
        "pacing_mode": sess.pacing_mode,
    }

    # Restore from checkpoint or fresh init
    if sess.checkpoint_data:
        await session_manager.restore_from_checkpoint(
            session_id, sess.checkpoint_data, questions, config)
    else:
        session_manager.create_session(session_id, questions, config)
        # Load all joined students from DB so they can answer
        stud_r = await db.execute(select(Student).where(Student.session_id == session_id))
        for s in stud_r.scalars().all():
            session_manager.add_student(session_id, s.id, s.name, s.roll_number)

    await db.execute(update(Session).where(Session.id == session_id)
                     .values(status="active", started_at=datetime.utcnow()))
    await db.commit()

    # Update in-memory state to active  ← THIS WAS THE BUG
    live_state = session_manager.get_session(session_id)
    if live_state:
        live_state["status"] = "active"

    await ws_manager.broadcast_all(session_id, {
        "type": "session_start",
        "data": {"session_id": session_id, "title": sess.title,
                 "server_time": time.time()}
    })

    state = session_manager.get_session(session_id)
    if state:
        # Push Q1 to each connected student via background task so the HTTP
        # handler returns immediately — avoids concurrent WS send races.
        async def push_q1_to_all():
            await asyncio.sleep(0.1)   # tiny delay to let HTTP response flush
            s = session_manager.get_session(session_id)
            if not s:
                return
            for sid in list(s["students"].keys()):
                st = s["students"].get(sid)
                if st:
                    st.current_index = 0
                nxt_q = session_manager.get_student_question(session_id, sid)
                if nxt_q:
                    await ws_manager.send_to_student(session_id, sid, {
                        "type": "question_push",
                        "data": {
                            "question_id": nxt_q.question_id,
                            "index": nxt_q.index,
                            "total": len(s["questions"]),
                            "text": nxt_q.text,
                            "options": nxt_q.options,
                            "time_limit": nxt_q.time_limit,
                            "start_time": time.time(),
                            "question_type": nxt_q.question_type,
                            "pacing_mode": "auto",
                        }
                    })
        asyncio.create_task(push_q1_to_all())

    return {"status": "active"}


@router.post("/sessions/{session_id}/pause")
async def pause_session(session_id: str, token: str = Query(...),
                        db: AsyncSession = Depends(get_db)):
    verify_admin(token)
    state = session_manager.get_session(session_id)
    if state:
        state["status"] = "paused"
    await db.execute(update(Session).where(Session.id == session_id)
                     .values(status="paused"))
    await db.commit()
    await ws_manager.broadcast_all(session_id, {"type": "pause",
                                                 "data": {"server_time": time.time()}})
    return {"status": "paused"}


@router.post("/sessions/{session_id}/resume")
async def resume_session(session_id: str, token: str = Query(...),
                         db: AsyncSession = Depends(get_db)):
    verify_admin(token)
    state = session_manager.get_session(session_id)
    if state:
        state["status"] = "active"
    await db.execute(update(Session).where(Session.id == session_id)
                     .values(status="active"))
    await db.commit()
    await ws_manager.broadcast_all(session_id, {"type": "resume",
                                                 "data": {"server_time": time.time()}})
    return {"status": "active"}


@router.post("/sessions/{session_id}/next_question")
async def push_next_question(session_id: str, token: str = Query(...),
                             db: AsyncSession = Depends(get_db)):
    verify_admin(token)
    state = session_manager.get_session(session_id)
    if not state:
        raise HTTPException(400, "Session not loaded in memory. Start the session first.")

    q = session_manager.next_question(session_id)
    if not q:
        raise HTTPException(400, "No more questions")

    payload = {
        "type": "question_push",
        "data": {
            "question_id": q.question_id,
            "index": q.index,
            "total": len(state["questions"]),
            "text": q.text,
            "options": q.options,
            "time_limit": q.time_limit,
            "start_time": q.start_time,
            "question_type": q.question_type,
        }
    }
    await ws_manager.broadcast_students(session_id, payload)
    await ws_manager.broadcast_admins(session_id, {**payload, "correct_index": q.correct_shuffled_index})

    # Auto-advance timer
    if q.time_limit > 0:
        async def auto_end():
            await asyncio.sleep(q.time_limit + 1)
            await _end_question(session_id, q.question_id)
        state["timer_task"] = asyncio.create_task(auto_end())

    return {"question_index": q.index, "question_id": q.question_id}


async def _end_question(session_id: str, question_id: str):
    """Called after timer ends — push leaderboard update."""
    state = session_manager.get_session(session_id)
    if not state:
        return
    cq = state.get("current_question")
    if not cq or cq.question_id != question_id:
        return

    board = session_manager.compute_leaderboard(session_id)
    await ws_manager.broadcast_all(session_id, {
        "type": "question_end",
        "data": {
            "question_id": question_id,
            "correct_index": cq.correct_shuffled_index,
            "correct_text": cq.options[cq.correct_shuffled_index],
        }
    })
    await ws_manager.broadcast_all(session_id, {
        "type": "leaderboard_update",
        "version": state["leaderboard_version"],
        "data": board,
    })


@router.post("/sessions/{session_id}/end_question")
async def end_question(session_id: str, token: str = Query(...)):
    verify_admin(token)
    state = session_manager.get_session(session_id)
    if not state or not state.get("current_question"):
        raise HTTPException(400, "No active question")
    if state.get("timer_task"):
        state["timer_task"].cancel()
    await _end_question(session_id, state["current_question"].question_id)
    return {"ok": True}


@router.post("/sessions/{session_id}/end")
async def end_session(session_id: str, token: str = Query(...),
                      db: AsyncSession = Depends(get_db)):
    verify_admin(token)
    state = session_manager.get_session(session_id)
    if state and state.get("timer_task"):
        state["timer_task"].cancel()

    # Flush buffer before computing results
    await submission_buffer.flush_now()

    # Compute & store results
    stud_r = await db.execute(select(Student).where(Student.session_id == session_id))
    students = stud_r.scalars().all()
    q_r = await db.execute(select(Question).where(Question.session_id == session_id))
    total_q = len(q_r.scalars().all())
    viol_r = await db.execute(select(Violation).where(Violation.session_id == session_id))
    violations = viol_r.scalars().all()

    viol_counts = {}
    for v in violations:
        viol_counts[v.student_id] = viol_counts.get(v.student_id, 0) + 1

    sorted_students = sorted(students, key=lambda s: (-s.score, -s.correct_count))
    for rank, s in enumerate(sorted_students, 1):
        r = Result(
            session_id=session_id, student_id=s.id,
            final_score=s.score, correct_count=s.correct_count,
            total_questions=total_q, rank=rank,
            violations_count=viol_counts.get(s.id, 0),
        )
        db.add(r)
        await db.execute(update(Student).where(Student.id == s.id)
                         .values(status="submitted", submitted_at=datetime.utcnow()))

    await db.execute(update(Session).where(Session.id == session_id)
                     .values(status="ended", ended_at=datetime.utcnow()))
    await db.commit()

    board = session_manager.compute_leaderboard(session_id) if state else []
    await ws_manager.broadcast_all(session_id, {
        "type": "exam_end",
        "data": {"leaderboard": board, "server_time": time.time()}
    })
    session_manager.remove_session(session_id)
    return {"status": "ended"}


# ── Monitoring ────────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/violations")
async def get_violations(session_id: str, token: str = Query(...),
                         db: AsyncSession = Depends(get_db)):
    verify_admin(token)
    r = await db.execute(select(Violation).where(Violation.session_id == session_id)
                         .order_by(Violation.occurred_at.desc()))
    viols = r.scalars().all()
    out = []
    for v in viols:
        s = await db.get(Student, v.student_id)
        out.append({
            "id": v.id, "student_name": s.name if s else "?",
            "roll_number": s.roll_number if s else "?",
            "type": v.violation_type, "strike": v.strike_number,
            "occurred_at": v.occurred_at.isoformat(),
        })
    return out


@router.post("/sessions/{session_id}/unlock_student")
async def unlock_student(session_id: str, student_id: str = Query(...),
                         reduce_marks: float = Query(0.0),
                         token: str = Query(...), db: AsyncSession = Depends(get_db)):
    verify_admin(token)
    student = session_manager.get_student(session_id, student_id)
    if student:
        student.status = "active"
        student.strike_count = 0
        student.score -= reduce_marks
        
    db_student = await db.get(Student, student_id)
    if db_student:
        db_student.status = "active"
        db_student.strike_count = 0
        db_student.score -= reduce_marks
        await db.commit()
        
    await ws_manager.send_to_student(session_id, student_id,
                                     {"type": "exam_unlocked", "data": {}})
                                     
    board = session_manager.compute_leaderboard(session_id)
    state = session_manager.get_session(session_id)
    if state:
        await ws_manager.broadcast_admins(session_id, {
            "type": "leaderboard_update",
            "version": state["leaderboard_version"],
            "data": board,
        })
    return {"ok": True}


# ── Export ────────────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/export")
async def export_results(session_id: str, token: str = Query(...),
                         db: AsyncSession = Depends(get_db)):
    verify_admin(token)
    r = await db.execute(select(Result).where(Result.session_id == session_id)
                         .order_by(Result.rank))
    results = r.scalars().all()

    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(["Rank", "Name", "Roll No", "Score",
                     "Correct", "Total Q", "Violations"])
    for res in results:
        s = await db.get(Student, res.student_id)
        writer.writerow([
            res.rank, s.name if s else "?", s.roll_number if s else "?",
            res.final_score, res.correct_count, res.total_questions,
            res.violations_count
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.read()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=results_{session_id}.csv"}
    )


# ── Student Search ────────────────────────────────────────────────────────────

@router.get("/students/search")
async def search_students(
    token: str = Query(...),
    name: str = Query(default=""),
    roll: str = Query(default=""),
    db: AsyncSession = Depends(get_db)
):
    verify_admin(token)
    if not name and not roll:
        return []

    stmt = select(Student)
    if name:
        stmt = stmt.where(Student.name.ilike(f"%{name}%"))
    if roll:
        stmt = stmt.where(Student.roll_number.ilike(f"%{roll}%"))
    result = await db.execute(stmt.order_by(Student.joined_at.desc()))
    students = result.scalars().all()

    out = []
    seen_keys = set()  # deduplicate by (name, roll_number)
    for s in students:
        key = (s.name.lower(), s.roll_number.lower())
        # Build a grouped record per unique student identity
        found = next((x for x in out if x["roll_number"].lower() == s.roll_number.lower()
                      and x["name"].lower() == s.name.lower()), None)

        sess = await db.get(Session, s.session_id)
        viol_r = await db.execute(
            select(Violation).where(
                Violation.student_id == s.id,
                Violation.session_id == s.session_id
            )
        )
        viols = viol_r.scalars().all()

        session_entry = {
            "session_id": s.session_id,
            "session_title": sess.title if sess else "?",
            "session_code": sess.session_code if sess else "?",
            "class_name": sess.class_name if sess else None,
            "category": sess.category if sess else None,
            "status": s.status,
            "score": s.score,
            "correct_count": s.correct_count,
            "strike_count": s.strike_count,
            "violations": [{"type": v.violation_type, "at": v.occurred_at.isoformat()} for v in viols],
            "joined_at": s.joined_at.isoformat() if s.joined_at else None,
        }

        if found:
            found["sessions"].append(session_entry)
            found["total_sessions"] = len(found["sessions"])
            found["total_score"] = round(sum(x["score"] for x in found["sessions"]), 1)
            found["total_violations"] = sum(len(x["violations"]) for x in found["sessions"])
        else:
            out.append({
                "name": s.name,
                "roll_number": s.roll_number,
                "total_sessions": 1,
                "total_score": round(s.score, 1),
                "total_violations": len(viols),
                "sessions": [session_entry],
            })

    return out
