"""
ExamLAN WebSocket Manager
Uses a per-student outgoing queue so that sends and receives
never happen concurrently on the same WebSocket object.
"""
import asyncio
import logging
from typing import Dict, Optional, Set
from fastapi import WebSocket

logger = logging.getLogger(__name__)

_SENTINEL = object()   # marks queue shutdown


class StudentConnection:
    """Wraps one student WebSocket with a dedicated send-queue."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self._q: asyncio.Queue = asyncio.Queue()
        self._pump_task: Optional[asyncio.Task] = None

    def start_pump(self):
        self._pump_task = asyncio.create_task(self._pump())

    async def _pump(self):
        """Drain the outgoing queue, sending one message at a time."""
        while True:
            item = await self._q.get()
            if item is _SENTINEL:
                break
            try:
                await self.ws.send_json(item)
            except Exception:
                break   # connection gone; exit pump

    async def send(self, msg: dict):
        """Enqueue a message (never awaits the WS directly)."""
        await self._q.put(msg)

    async def close(self):
        """Signal the pump to stop."""
        await self._q.put(_SENTINEL)
        if self._pump_task:
            try:
                await asyncio.wait_for(self._pump_task, timeout=2)
            except Exception:
                pass


class ConnectionManager:
    """
    Manages all live WebSocket connections.
    Students: each has a StudentConnection with a dedicated send-pump.
    Admins: also use a dedicated send-pump to avoid concurrent send errors.
    """

    def __init__(self):
        self._students: Dict[str, Dict[str, StudentConnection]] = {}
        self._admins: Dict[str, Set[StudentConnection]] = {}

    # ── Connect / disconnect ──────────────────────────────────────────────────

    async def connect_student(self, session_id: str, student_id: str, ws: WebSocket):
        await ws.accept()
        conn = StudentConnection(ws)
        conn.start_pump()
        self._students.setdefault(session_id, {})[student_id] = conn
        logger.info(f"Student {student_id} connected to session {session_id}")

    async def disconnect_student(self, session_id: str, student_id: str):
        conn = self._students.get(session_id, {}).pop(student_id, None)
        if conn:
            await conn.close()
        logger.info(f"Student {student_id} disconnected from session {session_id}")

    async def connect_admin(self, session_id: str, ws: WebSocket):
        await ws.accept()
        conn = StudentConnection(ws)
        conn.start_pump()
        self._admins.setdefault(session_id, set()).add(conn)
        logger.info(f"Admin connected to session {session_id}")

    def disconnect_admin(self, session_id: str, ws: WebSocket):
        admin_set = self._admins.get(session_id, set())
        to_remove = [c for c in admin_set if c.ws == ws]
        for c in to_remove:
            admin_set.discard(c)
            # We don't await c.close() here as disconnect_admin isn't async
            # The pump will naturally die if the WS is closed

    # ── Send helpers ──────────────────────────────────────────────────────────

    async def send_to_student(self, session_id: str, student_id: str, msg: dict):
        conn = self._students.get(session_id, {}).get(student_id)
        if conn:
            await conn.send(msg)   # just enqueues; never blocks on WS

    async def broadcast_students(self, session_id: str, msg: dict):
        conns = list(self._students.get(session_id, {}).values())
        await asyncio.gather(*[c.send(msg) for c in conns], return_exceptions=True)

    async def broadcast_admins(self, session_id: str, msg: dict):
        conns = list(self._admins.get(session_id, set()))
        await asyncio.gather(*[c.send(msg) for c in conns], return_exceptions=True)

    async def broadcast_all(self, session_id: str, msg: dict):
        await self.broadcast_students(session_id, msg)
        await self.broadcast_admins(session_id, msg)

    # ── Stats ─────────────────────────────────────────────────────────────────

    def connected_count(self, session_id: str) -> int:
        return len(self._students.get(session_id, {}))

    def connected_ids(self, session_id: str) -> list:
        return list(self._students.get(session_id, {}).keys())


# Singleton
ws_manager = ConnectionManager()
