#!/usr/bin/env bash
# ExamLAN — Local LAN start script
set -e

BACKEND_DIR="$(cd "$(dirname "$0")/backend" && pwd)"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  📋  ExamLAN — Offline Exam Server"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Python venv ───────────────────────────────────
if [ ! -d "$BACKEND_DIR/.venv" ]; then
  echo "→ Creating Python virtual environment…"
  python3 -m venv "$BACKEND_DIR/.venv"
fi

source "$BACKEND_DIR/.venv/bin/activate"

echo "→ Installing dependencies…"
pip install -q -r "$BACKEND_DIR/requirements.txt"

# ── Detect LAN IP ─────────────────────────────────
LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || \
         ip route get 1 2>/dev/null | awk '{print $7}' | head -1 || \
         echo "127.0.0.1")

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅  Server starting on LAN"
echo ""
echo "  Student Portal : http://${LAN_IP}:8000/"
echo "  Admin Dashboard: http://${LAN_IP}:8000/admin.html"
echo "  API Docs       : http://${LAN_IP}:8000/docs"
echo ""
echo "  Share the Student URL with students."
echo "  Default admin token: exam-admin-secret"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

cd "$BACKEND_DIR"
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
