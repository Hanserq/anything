# ExamLAN: Offline LAN Examination System

ExamLAN is a robust, highly concurrent, entirely offline LAN-based real-time examination system. It is designed specifically for internet-independent educational environments where hundreds of students can take assessments simultaneously over a local Wi-Fi network without requiring an active internet connection.

By leveraging mDNS zero-configuration networking, WebSocket concurrency, and an asynchronous memory submission buffer, ExamLAN guarantees stability, high performance, and ease of use in resource-constrained environments.

---

## Key Features

- **True Offline Capability:** Operates entirely on your local area network (LAN) without needing an external internet connection.
- **Zero-Configuration Networking (mDNS):** Employs ZeroConf so students can connect using a simple hostname (e.g., `http://examlan.local:8000`) instead of typing out complex, shifting IP addresses.
- **High Concurrency WebSockets:** Ensures reliable student-server real-time interaction during high-load concurrent exams.
- **Asynchronous Submission Buffer:** Uses an in-memory queue to temporarily buffer student answers, executing batch writes to the database to prevent SQLite locking under extreme load.
- **Admin Dashboard & Student Portal:** Distinct, easy-to-use interfaces for exam invigilators to manage states and for students to complete assessments.
- **No-Cache Middleware:** Prevents browsers (especially Safari) from caching API endpoints, ensuring live state synchronization.

---

## Tech Stack

### Backend
- **Framework:** [FastAPI](https://fastapi.tiangolo.com/) (Python)
- **Concurrency & Serving:** `uvicorn[standard]` and asynchronous handlers (`asyncio`).
- **Real-Time Communication:** WebSockets (`websockets` library).
- **Network Discovery:** `zeroconf` (mDNS) to broadcast `_http._tcp.local.` services.
- **Database:** SQLite with `aiosqlite` and `sqlalchemy` (async engine). Database uses WAL (Write-Ahead Logging) mode for enhanced concurrency.

### Frontend
- **Stack:** Plain HTML, Vanilla JS, and CSS.
- **Service Worker:** Includes a `sw.js` and `manifest.json` for potential PWA installation or caching strategies.
- **Serving:** Static files are served directly by the FastAPI backend on the exact same port to avoid complex CORS or routing setups.

---

## Architecture Highlights

1. **Submission Buffer (`buffer.py`)**
   Under high load, individual writes to SQLite can cause database locks. ExamLAN implements an asynchronous `SubmissionBuffer` that stores student answers in memory, instantly calculates scores, and performs a bulk insert (flush) to the database every 5 seconds.
   
2. **WebSocket Concurrency (`routers/ws.py`)**
   Provides a real-time, bi-directional event stream. Instead of standard HTTP polling, clients maintain an open WebSocket connection for real-time exam state updates (e.g., "Exam Started", "Time's up", live score broadcasts).

3. **mDNS Registration (`main.py`)**
   Upon startup, the server automatically resolves the host machine's LAN IP address and broadcasts a ZeroConf service under `examlan.local`. This enables cross-device discovery on the same subnet without manual IP sharing.

---

## Step-by-Step Usage Guide

### Prerequisites
- macOS or Linux (or WSL on Windows)
- Python 3.8 or higher installed
- All devices connected to the **same Wi-Fi router/network**

### 1. Start the Server
Open your terminal, navigate to the `exam-system` directory, and execute the startup script. This script automates virtual environment creation, dependency installation, and server startup.

```bash
cd exam-system
./start.sh
```

**What happens next?**
- Creates a Python virtual environment (`.venv`) if one does not exist.
- Installs all dependencies from `backend/requirements.txt`.
- Finds the current LAN IP and starts the FastAPI/Uvicorn server on port `8000`.
- Broadcasts the `examlan.local` mDNS service.

### 2. Connect Students
Once the server initializes, the terminal will log the connection URLs. Ask your students to connect to the same network and navigate to:

- **Student Portal:** `http://examlan.local:8000/`

*(Fallback: If mDNS is unsupported by a student's device, they can use the exact LAN IP address printed in the host terminal, e.g., `http://192.168.1.5:8000/`)*

### 3. Access the Admin Dashboard
To control the exam flow, view live student connectivity, and manage results, the invigilator can access the dashboard from the host machine:

- **Admin URL:** `http://localhost:8000/admin.html`
- **Default Token:** `exam-admin-secret` *(This is hardcoded on initialization but can be modified in the database or `.env`)*

---

## Project Structure

```
exam-system/
│
├── backend/
│   ├── .env                 # Environment variables (Optional)
│   ├── requirements.txt     # Python dependencies
│   ├── main.py              # FastAPI application entry point & mDNS logic
│   ├── database.py          # SQLAlchemy async setup, WAL pragma, schema init
│   ├── models.py            # SQLAlchemy ORM models (Student, Submission, etc.)
│   ├── buffer.py            # Async submission buffer for batch DB writing
│   └── routers/
│       ├── admin.py         # REST endpoints for admin dashboard
│       └── ws.py            # WebSocket endpoints for real-time exam sync
│
├── frontend/
│   ├── index.html           # Student portal entry point
│   ├── admin.html           # Admin dashboard entry point
│   ├── manifest.json        # Web app manifest
│   ├── sw.js                # Service Worker
│   ├── css/                 # Stylesheets
│   └── js/                  # Client-side logic
│
├── start.sh                 # Unified bootstrap and launch script
└── README.md                # Project documentation
```

---

## Troubleshooting

- **mDNS Not Resolving:** Ensure the router doesn't have "AP Isolation" or "Client Isolation" turned on, as this blocks peer-to-peer communication on the network.
- **Server Locks / DB Issues:** Check the terminal output. The database uses SQLite WAL mode. Ensure the server has write permissions to the directory where `exam.db` is created.
- **Stale Content / Browser Caching:** ExamLAN implements no-cache headers for API routes. If the UI acts stale, ask students to hard-refresh the page (`Ctrl + Shift + R` or `Cmd + Shift + R`).
