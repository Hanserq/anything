import asyncio
import time
import uuid
import logging
from typing import List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class SubmissionBuffer:
    """
    In-memory buffer: Submission → Buffer → Batch SQLite write.
    Prevents excessive SQLite locking and allows instant in-memory scoring.
    """

    def __init__(self, flush_interval: float = 5.0):
        self._buffer: List[dict] = []
        self._lock = asyncio.Lock()
        self._flush_interval = flush_interval
        self._flush_task: Optional[asyncio.Task] = None
        self._submitted_keys: set = set()   # (student_id, question_id) dedup
        self._total_flushed = 0

    async def start(self):
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.info(f"Buffer started — flush interval={self._flush_interval}s")

    async def stop(self):
        if self._flush_task:
            self._flush_task.cancel()
        await self.flush_now()

    async def add(self, submission: dict) -> bool:
        """Returns False if duplicate, True if accepted."""
        key = (submission["student_id"], submission["question_id"])
        async with self._lock:
            if key in self._submitted_keys:
                return False
            self._submitted_keys.add(key)
            submission.setdefault("id", str(uuid.uuid4()))
            submission["buffered_at"] = time.time()
            self._buffer.append(submission)
        return True

    def has_submitted(self, student_id: str, question_id: str) -> bool:
        return (student_id, question_id) in self._submitted_keys

    def pending_count(self) -> int:
        return len(self._buffer)

    async def _flush_loop(self):
        while True:
            await asyncio.sleep(self._flush_interval)
            try:
                await self.flush_now()
            except Exception as e:
                logger.error(f"Flush error: {e}")

    async def flush_now(self):
        async with self._lock:
            if not self._buffer:
                return
            items = self._buffer.copy()
            self._buffer.clear()

        await self._write_batch(items)
        self._total_flushed += len(items)
        logger.info(f"Flushed {len(items)} submissions (total={self._total_flushed})")

    async def _write_batch(self, items: List[dict]):
        from database import AsyncSessionLocal
        from models import Submission, Student
        from sqlalchemy import update

        async with AsyncSessionLocal() as db:
            try:
                for item in items:
                    sub = Submission(
                        id=item["id"],
                        session_id=item["session_id"],
                        student_id=item["student_id"],
                        question_id=item["question_id"],
                        selected_option=item.get("selected_option"),
                        actual_option=item.get("actual_option"),
                        is_correct=item.get("is_correct", False),
                        score_awarded=item.get("score_awarded", 0.0),
                        time_taken=item.get("time_taken", 0.0),
                        submitted_at=datetime.utcnow(),
                        is_cached=item.get("is_cached", False),
                    )
                    db.add(sub)
                    if item.get("is_correct"):
                        await db.execute(
                            update(Student)
                            .where(Student.id == item["student_id"])
                            .values(
                                score=Student.score + item.get("score_awarded", 0.0),
                                correct_count=Student.correct_count + 1,
                            )
                        )
                await db.commit()
            except Exception as e:
                await db.rollback()
                logger.error(f"Batch DB write failed: {e}")
                raise


# Singleton
submission_buffer = SubmissionBuffer(flush_interval=5.0)
