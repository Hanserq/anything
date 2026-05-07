import asyncio
import json
import logging
from typing import Dict, Set
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages all live WebSocket connections.
    Students connect to /ws/student/{session_id}
    Admins connect to  /ws/admin/{session_id}
    """

    def __init__(self):
        # session_id -> {student_id -> WebSocket}
        self._students: Dict[str, Dict[str, WebSocket]] = {}
        # session_id -> {WebSocket}
        self._admins: Dict[str, Set[WebSocket]] = {}

    # ── Connect / disconnect ──────────────────────────────────────────────────

    async def connect_student(self, session_id: str, student_id: str, ws: WebSocket):
        await ws.accept()
        self._students.setdefault(session_id, {})[student_id] = ws
        logger.info(f"Student {student_id} connected to session {session_id}")

    def disconnect_student(self, session_id: str, student_id: str):
        sess = self._students.get(session_id, {})
        sess.pop(student_id, None)
        logger.info(f"Student {student_id} disconnected from session {session_id}")

    async def connect_admin(self, session_id: str, ws: WebSocket):
        await ws.accept()
        self._admins.setdefault(session_id, set()).add(ws)
        logger.info(f"Admin connected to session {session_id}")

    def disconnect_admin(self, session_id: str, ws: WebSocket):
        self._admins.get(session_id, set()).discard(ws)

    # ── Send helpers ──────────────────────────────────────────────────────────

    async def send_to_student(self, session_id: str, student_id: str, msg: dict):
        ws = self._students.get(session_id, {}).get(student_id)
        if ws:
            try:
                await ws.send_json(msg)
            except Exception:
                self.disconnect_student(session_id, student_id)

    async def broadcast_students(self, session_id: str, msg: dict):
        dead = []
        for sid, ws in list(self._students.get(session_id, {}).items()):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(sid)
        for sid in dead:
            self.disconnect_student(session_id, sid)

    async def broadcast_admins(self, session_id: str, msg: dict):
        dead = []
        for ws in list(self._admins.get(session_id, set())):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect_admin(session_id, ws)

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
