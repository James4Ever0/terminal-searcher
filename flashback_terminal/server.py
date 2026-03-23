"""FastAPI server for flashback-terminal."""

import asyncio
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from flashback_terminal.api.websocket import TerminalWebSocketHandler
from flashback_terminal.config import get_config
from flashback_terminal.database import Database
from flashback_terminal.retention import RetentionManager
from flashback_terminal.search import SearchEngine
from flashback_terminal.terminal import TerminalManager

# Global instances
db: Optional[Database] = None
terminal_manager: Optional[TerminalManager] = None
ws_handler: Optional[TerminalWebSocketHandler] = None
search_engine: Optional[SearchEngine] = None
retention_manager: Optional[RetentionManager] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global db, terminal_manager, ws_handler, search_engine, retention_manager

    config = get_config()

    db = Database(config.db_path)
    terminal_manager = TerminalManager(db)
    ws_handler = TerminalWebSocketHandler(terminal_manager, db)

    if config.is_module_enabled("history_keeper"):
        search_engine = SearchEngine(db)

    if config.is_worker_enabled("retention"):
        retention_manager = RetentionManager(db)
        asyncio.create_task(retention_scheduler())

    print("[Server] flashback-terminal started")
    yield
    print("[Server] Shutting down...")


async def retention_scheduler():
    """Background task for retention management."""
    config = get_config()
    interval = config.get("workers.retention.check_interval_seconds", 3600)

    while True:
        await asyncio.sleep(interval)
        if retention_manager:
            retention_manager.run_cleanup()


app = FastAPI(title="flashback-terminal", lifespan=lifespan)

# Static files
import os

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main terminal UI."""
    import os

    index_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as f:
            return f.read()
    return "<h1>flashback-terminal</h1><p>Static files not found</p>"


@app.websocket("/ws/terminal/{session_uuid}")
async def terminal_websocket(websocket: WebSocket, session_uuid: str):
    """WebSocket endpoint for terminal sessions."""
    if ws_handler:
        await ws_handler.handle(websocket, session_uuid)


@app.get("/api/sessions")
async def list_sessions(
    status: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """List terminal sessions."""
    sessions = db.list_sessions(status=status, limit=limit, offset=offset)
    return {
        "sessions": [
            {
                "id": s.id,
                "uuid": s.uuid,
                "name": s.name,
                "profile_name": s.profile_name,
                "created_at": s.created_at.isoformat(),
                "status": s.status,
                "last_cwd": s.last_cwd,
            }
            for s in sessions
        ]
    }


@app.post("/api/sessions")
async def create_session(profile: str = "default", name: Optional[str] = None):
    """Create a new terminal session."""
    session = terminal_manager.create_session(profile_name=profile, name=name)
    if not session:
        raise HTTPException(status_code=500, detail="Failed to create session")

    return {
        "session_id": session.session_id,
        "uuid": session.uuid,
        "name": name or f"Terminal {session.session_id}",
    }


@app.get("/api/sessions/{session_uuid}")
async def get_session(session_uuid: str):
    """Get session details."""
    session = db.get_session_by_uuid(session_uuid)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "id": session.id,
        "uuid": session.uuid,
        "name": session.name,
        "profile_name": session.profile_name,
        "created_at": session.created_at.isoformat(),
        "status": session.status,
        "last_cwd": session.last_cwd,
    }


@app.put("/api/sessions/{session_uuid}")
async def update_session(session_uuid: str, name: str):
    """Update session (rename)."""
    session = db.get_session_by_uuid(session_uuid)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    db.update_session(session.id, name=name)
    return {"success": True}


@app.delete("/api/sessions/{session_uuid}")
async def delete_session(session_uuid: str):
    """Delete a session."""
    session = db.get_session_by_uuid(session_uuid)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    terminal_manager.close_session(session_uuid)
    db.delete_session(session.id)
    return {"success": True}


@app.post("/api/sessions/{session_uuid}/restore")
async def restore_session(session_uuid: str):
    """Restore an archived session."""
    raise HTTPException(status_code=501, detail="Archive restoration not yet implemented")


@app.get("/api/profiles")
async def list_profiles():
    """List available terminal profiles."""
    config = get_config()
    profiles = config.get("profiles", [])
    return {"profiles": profiles}


@app.post("/api/search")
async def search(
    query: str,
    mode: str = "text",
    scope: str = "all",
    session_ids: Optional[List[str]] = None,
    limit: int = 50,
):
    """Search terminal history."""
    if not search_engine:
        raise HTTPException(status_code=503, detail="Search not available")

    target_session_ids = None
    if scope == "current" and session_ids:
        target_session_ids = []
        for uuid in session_ids:
            session = db.get_session_by_uuid(uuid)
            if session:
                target_session_ids.append(session.id)

    results = search_engine.search(
        query=query, mode=mode, scope=scope, session_ids=target_session_ids, limit=limit
    )

    return {"query": query, "mode": mode, "results": results}


@app.get("/api/history/{session_uuid}")
async def get_history(
    session_uuid: str,
    from_seq: Optional[int] = None,
    to_seq: Optional[int] = None,
):
    """Get terminal history for a session."""
    session = db.get_session_by_uuid(session_uuid)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    outputs = db.get_terminal_output(session.id, from_seq, to_seq)
    return {
        "session_uuid": session_uuid,
        "outputs": [
            {
                "sequence_num": o.sequence_num,
                "timestamp": o.timestamp.isoformat(),
                "content": o.content,
                "content_type": o.content_type,
            }
            for o in outputs
        ],
    }


@app.get("/api/screenshots/{session_uuid}")
async def list_screenshots(session_uuid: str, limit: int = 100):
    """List screenshots for a session."""
    session = db.get_session_by_uuid(session_uuid)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    screenshots = db.get_screenshots(session.id, limit)
    return {
        "session_uuid": session_uuid,
        "screenshots": [
            {
                "id": s.id,
                "timestamp": s.timestamp.isoformat(),
                "file_path": s.file_path,
                "width": s.width,
                "height": s.height,
            }
            for s in screenshots
        ],
    }


@app.post("/api/retention/run")
async def run_retention():
    """Manually trigger retention cleanup."""
    if not retention_manager:
        raise HTTPException(status_code=503, detail="Retention not enabled")

    retention_manager.run_cleanup()
    return {"success": True}
