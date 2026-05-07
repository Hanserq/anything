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

    def to_leaderboard_entry(self, rank: int) -> dict:
        return {
            "rank": rank,
            "student_id": self.student_id,
            "name": self.name,
            "roll_number": self.roll_number,
            "score": self.score,
            "correct_count": self.correct_count,
            "status": self.status,
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

        q: QuestionState = state["current_question"]
        if not q or q.question_id != question_id:
            return None

        actual_option = q.option_map[selected_shuffled]
        is_correct = (selected_shuffled == q.correct_shuffled_index)

        # Speed bonus: up to 3 extra points if answered in first 30% of time
        speed_bonus = 0.0
        if is_correct and q.time_limit > 0:
            ratio = max(0, 1 - time_taken / q.time_limit)
            speed_bonus = round(ratio * 3, 2)

        score_awarded = (q.points + speed_bonus) if is_correct else 0.0

        student.answered_ids.add(question_id)
        if is_correct:
            student.score += score_awarded
            student.correct_count += 1

        return {
            "session_id": session_id,
            "student_id": student_id,
            "question_id": question_id,
            "selected_option": selected_shuffled,
            "actual_option": actual_option,
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
        return q

    def current_question(self, session_id: str) -> Optional[QuestionState]:
        state = self._sessions.get(session_id)
        return state["current_question"] if state else None

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
            "students": {
                sid: {
                    "score": st.score,
                    "correct_count": st.correct_count,
                    "strike_count": st.strike_count,
                    "status": st.status,
                    "answered_ids": list(st.answered_ids),
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

    async def restore_from_checkpoint(self, session_id: str, cp_json: str,
                                      questions: List[dict], config: dict):
        """Rebuild in-memory state from a saved checkpoint."""
        cp = json.loads(cp_json)
        self.create_session(session_id, questions, config)
        state = self._sessions[session_id]
        state["status"] = cp.get("status", "waiting")
        state["current_index"] = cp.get("current_index", -1)
        state["leaderboard"] = cp.get("leaderboard", [])
        state["leaderboard_version"] = cp.get("leaderboard_version", 0)

        # Restore question pointer
        idx = state["current_index"]
        if 0 <= idx < len(state["questions"]):
            state["current_question"] = state["questions"][idx]

        for sid, sdata in cp.get("students", {}).items():
            st = state["students"].get(sid)
            if st:
                st.score = sdata.get("score", 0.0)
                st.correct_count = sdata.get("correct_count", 0)
                st.strike_count = sdata.get("strike_count", 0)
                st.status = sdata.get("status", "active")
                st.answered_ids = set(sdata.get("answered_ids", []))


# Singleton
session_manager = SessionManager()
