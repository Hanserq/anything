import asyncio
import json
import random
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class StudentState:
    student_id: str
    name: str
    roll_number: str
    score: float = 0.0
    correct_count: int = 0
    strike_count: int = 0
    status: str = "joined"          # joined/active/disconnected/locked/submitted
    answered_ids: set = field(default_factory=set)
    current_index: int = 0
    current_question_start_time: float = field(default_factory=time.time)
    joined_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    def to_leaderboard_entry(self, rank: int) -> dict:
        return {
            "rank": rank,
            "student_id": self.student_id,
            "name": self.name,
            "roll_number": self.roll_number,
            "score": self.score,
            "correct_count": self.correct_count,
            "status": self.status,
            "time_taken": (self.finished_at - self.joined_at) if self.finished_at else None,
        }


@dataclass
class QuestionState:
    question_id: str
    index: int
    text: str
    options: List[str]              # shuffled options shown to students
    option_map: List[int]           # option_map[i] = original index of shuffled[i]
    correct_shuffled_index: int     # correct index in shuffled order
    points: float
    time_limit: int
    start_time: float = 0.0
    question_type: str = "mcq"


class SessionManager:
    """Holds live in-memory state for all active exam sessions."""

    def __init__(self):
        # session_id -> session state dict
        self._sessions: Dict[str, dict] = {}
        self._checkpoint_tasks: Dict[str, asyncio.Task] = {}

    # ── Session lifecycle ─────────────────────────────────────────────────────

    def create_session(self, session_id: str, questions: List[dict],
                       config: dict) -> dict:
        """Initialise in-memory state for a session."""
        q_list = list(questions)
        if config.get("randomize_questions", True):
            random.shuffle(q_list)

        prepared = []
        for idx, q in enumerate(q_list):
            opts = list(q["options"])
            mapping = list(range(len(opts)))
            if config.get("randomize_options", True):
                combined = list(zip(opts, mapping))
                random.shuffle(combined)
                opts, mapping = zip(*combined)
                opts, mapping = list(opts), list(mapping)
            correct_shuffled = mapping.index(q["correct_index"])
            prepared.append(QuestionState(
                question_id=q["id"],
                index=idx,
                text=q["text"],
                options=opts,
                option_map=mapping,
                correct_shuffled_index=correct_shuffled,
                points=q.get("points", 10.0),
                time_limit=q.get("time_limit", config.get("per_question_time", 30)),
                question_type=q.get("question_type", "mcq"),
            ))

        state = {
            "session_id": session_id,
            "status": "waiting",
            "questions": prepared,
            "current_index": -1,
            "current_question": None,
            "students": {},          # student_id -> StudentState
            "leaderboard": [],
            "leaderboard_version": 0,
            "config": config,
            "timer_task": None,
        }
        self._sessions[session_id] = state
        self._start_checkpoint(session_id)
        return state

    def get_session(self, session_id: str) -> Optional[dict]:
        return self._sessions.get(session_id)

    def remove_session(self, session_id: str):
        self._sessions.pop(session_id, None)
        task = self._checkpoint_tasks.pop(session_id, None)
        if task:
            task.cancel()

    # ── Student management ────────────────────────────────────────────────────

    def add_student(self, session_id: str, student_id: str, name: str,
                    roll_number: str) -> Optional[StudentState]:
        state = self._sessions.get(session_id)
        if not state:
            return None
        st = StudentState(student_id=student_id, name=name, roll_number=roll_number)
        state["students"][student_id] = st
        return st

    def get_student(self, session_id: str, student_id: str) -> Optional[StudentState]:
        state = self._sessions.get(session_id)
        return state["students"].get(student_id) if state else None

    def record_answer(self, session_id: str, student_id: str,
                      question_id: str, selected_shuffled: int,
                      time_taken: float) -> Optional[dict]:
        """
        Validates answer, computes score.
        Returns submission dict or None if invalid/duplicate.
        """
        state = self._sessions.get(session_id)
        if not state or state["status"] != "active":
            return None
        student = state["students"].get(student_id)
        if not student or question_id in student.answered_ids:
            return None

        q: QuestionState = state["questions"][student.current_index] if student.current_index < len(state["questions"]) else None

        if not q or q.question_id != question_id:
            return None

        if selected_shuffled < 0:
            actual_option = -1
            is_correct = False
        else:
            try:
                actual_option = q.option_map[selected_shuffled]
                is_correct = (selected_shuffled == q.correct_shuffled_index)
            except IndexError:
                actual_option = -1
                is_correct = False

        # Server-side time validation
        server_elapsed = time.time() - getattr(student, "current_question_start_time", time.time())
        # Client could be slightly slower due to network, so take the min, but enforce server bound
        validated_time_taken = max(0.0, min(time_taken, server_elapsed))

        # Speed bonus: up to 3 extra points if answered in first 30% of time
        speed_bonus = 0.0
        if is_correct and q.time_limit > 0:
            ratio = max(0, 1 - validated_time_taken / q.time_limit)
            speed_bonus = round(ratio * 3, 2)

        if validated_time_taken < 1.5:
            logger.warning(f"Speed Anomaly: Student {student_id} answered in {validated_time_taken:.2f}s")
            # Can also trigger self.record_violation(session_id, student_id) here if desired.

        if is_correct:
            score_awarded = q.points + speed_bonus
        else:
            # Negative Marking: -0.33 per wrong answer
            score_awarded = -0.33

        student.answered_ids.add(question_id)
        student.score += score_awarded
        if is_correct:
            student.correct_count += 1

        # Always advance the index — get_student_question returns None when past the end
        student.current_index += 1

        # Mark as finished if this was the last question
        if q.index >= len(state["questions"]) - 1:
            student.finished_at = time.time()
            student.status = "submitted"

        return {
            "session_id": session_id,
            "student_id": student_id,
            "question_id": question_id,
            "selected_option": selected_shuffled,
            "actual_option": actual_option,
            "correct_shuffled": q.correct_shuffled_index,
            "is_correct": is_correct,
            "score_awarded": score_awarded,
            "time_taken": time_taken,
        }

    # ── Question flow ─────────────────────────────────────────────────────────

    def next_question(self, session_id: str) -> Optional[QuestionState]:
        state = self._sessions.get(session_id)
        if not state:
            return None
        nxt = state["current_index"] + 1
        if nxt >= len(state["questions"]):
            return None
        state["current_index"] = nxt
        q = state["questions"][nxt]
        q.start_time = time.time()
        state["current_question"] = q
        state["status"] = "active"
        
        # Update start_time for all students for validation
        for st in state["students"].values():
            st.current_question_start_time = q.start_time
            
        return q

    def current_question(self, session_id: str) -> Optional[QuestionState]:
        state = self._sessions.get(session_id)
        return state["current_question"] if state else None

    def get_student_question(self, session_id: str, student_id: str) -> Optional[QuestionState]:
        state = self._sessions.get(session_id)
        if not state: return None
        student = state["students"].get(student_id)
        if not student: return None
        if student.current_index >= len(state["questions"]): return None
        return state["questions"][student.current_index]

    # ── Leaderboard ───────────────────────────────────────────────────────────

    def compute_leaderboard(self, session_id: str) -> List[dict]:
        state = self._sessions.get(session_id)
        if not state:
            return []
        students = list(state["students"].values())
        students.sort(key=lambda s: (-s.score, -s.correct_count))
        board = [s.to_leaderboard_entry(i + 1) for i, s in enumerate(students)]
        state["leaderboard_version"] += 1
        state["leaderboard"] = board
        return board

    # ── Violation handling ────────────────────────────────────────────────────

    def record_violation(self, session_id: str, student_id: str) -> int:
        """Returns new strike count."""
        student = self.get_student(session_id, student_id)
        if not student:
            return 0
        student.strike_count += 1
        max_s = self._sessions[session_id]["config"].get("max_strikes", 3)
        if student.strike_count >= max_s:
            student.status = "locked"
        return student.strike_count

    # ── Checkpoint / recovery ─────────────────────────────────────────────────

    def _start_checkpoint(self, session_id: str):
        task = asyncio.create_task(self._checkpoint_loop(session_id))
        self._checkpoint_tasks[session_id] = task

    async def _checkpoint_loop(self, session_id: str):
        while True:
            await asyncio.sleep(30)
            await self._save_checkpoint(session_id)

    async def _save_checkpoint(self, session_id: str):
        state = self._sessions.get(session_id)
        if not state:
            return
        q = state["current_question"]
        cp = {
            "status": state["status"],
            "current_index": state["current_index"],
            "current_question_id": q.question_id if q else None,
            "leaderboard": state["leaderboard"],
            "leaderboard_version": state["leaderboard_version"],
            "questions": [
                {
                    "question_id": q_obj.question_id,
                    "index": q_obj.index,
                    "text": q_obj.text,
                    "options": q_obj.options,
                    "option_map": q_obj.option_map,
                    "correct_shuffled_index": q_obj.correct_shuffled_index,
                    "points": q_obj.points,
                    "time_limit": q_obj.time_limit,
                    "question_type": q_obj.question_type,
                }
                for q_obj in state["questions"]
            ],
            "students": {
                sid: {
                    "score": st.score,
                    "correct_count": st.correct_count,
                    "strike_count": st.strike_count,
                    "status": st.status,
                    "answered_ids": list(st.answered_ids),
                    "name": st.name,
                    "roll_number": st.roll_number,
                    "current_index": st.current_index,
                }
                for sid, st in state["students"].items()
            },
        }
        from database import AsyncSessionLocal
        from models import Session
        from sqlalchemy import update
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Session)
                .where(Session.id == session_id)
                .values(checkpoint_data=json.dumps(cp))
            )
            await db.commit()
        logger.debug(f"Checkpoint saved for session {session_id}")

    async def restore_from_checkpoint(self, session_id: str, cp_json: str, config: dict):
        """Rebuild in-memory state from a saved checkpoint."""
        cp = json.loads(cp_json)
        
        prepared = []
        for qdata in cp.get("questions", []):
            prepared.append(QuestionState(
                question_id=qdata["question_id"],
                index=qdata["index"],
                text=qdata["text"],
                options=qdata["options"],
                option_map=qdata["option_map"],
                correct_shuffled_index=qdata["correct_shuffled_index"],
                points=qdata["points"],
                time_limit=qdata["time_limit"],
                question_type=qdata.get("question_type", "mcq"),
            ))

        state = {
            "session_id": session_id,
            "status": cp.get("status", "waiting"),
            "questions": prepared,
            "current_index": cp.get("current_index", -1),
            "current_question": None,
            "students": {},
            "leaderboard": cp.get("leaderboard", []),
            "leaderboard_version": cp.get("leaderboard_version", 0),
            "config": config,
            "timer_task": None,
        }
        self._sessions[session_id] = state

        # Restore question pointer
        idx = state["current_index"]
        if 0 <= idx < len(state["questions"]):
            state["current_question"] = state["questions"][idx]

        for sid, sdata in cp.get("students", {}).items():
            st = StudentState(
                student_id=sid,
                name=sdata.get("name", "?"),
                roll_number=sdata.get("roll_number", "?"),
                score=sdata.get("score", 0.0),
                correct_count=sdata.get("correct_count", 0),
                strike_count=sdata.get("strike_count", 0),
                status=sdata.get("status", "active"),
                answered_ids=set(sdata.get("answered_ids", [])),
                current_index=sdata.get("current_index", 0)
            )
            state["students"][sid] = st
            
        self._start_checkpoint(session_id)


# Singleton
session_manager = SessionManager()
