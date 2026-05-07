import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from database import init_db
from buffer import submission_buffer
from routers import admin, ws as ws_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initialising database...")
    await init_db()
    logger.info("Starting submission buffer...")
    await submission_buffer.start()
    logger.info("🚀 Exam server ready")
    yield
    # Shutdown
    logger.info("Flushing remaining submissions...")
    await submission_buffer.stop()
    logger.info("Server shutdown complete")


app = FastAPI(
    title="Offline Exam System",
    description="LAN-based real-time examination platform",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST routers
app.include_router(admin.router)
app.include_router(ws_router.router)

# ── Middleware for No-Cache (Safari fix) ───────────────────────────────────
@app.middleware("http")
async def add_no_cache_header(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# ── Health Check ─────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "buffer_pending": submission_buffer.pending_count()}


# Serve frontend static files (must be mounted LAST so API routes take priority)
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")



if __name__ == "__main__":
    import uvicorn
    host = os.getenv("SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("SERVER_PORT", 8000))
    uvicorn.run("main:app", host=host, port=port, reload=False)
