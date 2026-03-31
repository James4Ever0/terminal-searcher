"""FastAPI server for flashback-terminal."""

import asyncio
import os
import threading
import traceback
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, status
from fastapi.responses import HTMLResponse,  JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel


from flashback_terminal.api.websocket import TerminalWebSocketHandler
from flashback_terminal.config import get_config
from flashback_terminal.database import Database
from flashback_terminal.logger import Logger, log_function, logger
from flashback_terminal.retention import RetentionManager
from flashback_terminal.search import SearchEngine
from flashback_terminal.terminal import TerminalManager
from flashback_terminal.workers.capture_worker import CaptureWorkerScheduler

# Global instances
db: Optional[Database] = None
terminal_manager: Optional[TerminalManager] = None
ws_handler: Optional[TerminalWebSocketHandler] = None
search_engine: Optional[SearchEngine] = None
retention_manager: Optional[RetentionManager] = None
capture_scheduler: Optional[CaptureWorkerScheduler] = None


class SearchRequest(BaseModel):
    query: str
    mode: str = "text"
    scope: str = "all"
    session_ids: Optional[List[str]] = None
    limit: int = 50
    order_by: str = "relevance"  # "relevance", "time", "session_name", "hybrid"
    time_range: Optional[str] = None  # "1h", "24h", "7d", "30d", "all"
    filter_inactive: bool = False




@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global db, terminal_manager, ws_handler, search_engine, retention_manager

    logger.info("Starting flashback-terminal lifespan manager")

    config = get_config()
    
    if os.environ.get('CLI_VERBOSITY'):
        config.set("logging.verbosity", int(os.environ.get('CLI_VERBOSITY')))
        logger.debug(f"Using CLI verbosity: {config.verbosity}")

    logger.info(f"Configuration loaded: verbosity={config.verbosity}")
    logger.debug(f"Data directory: {config.data_dir}")

    logger.info("Initializing database...")
    db = Database(config.db_path)
    await db.init_db()
    logger.info(f"Database initialized: {config.db_path}")

    logger.info("Initializing terminal manager...")
    terminal_manager = TerminalManager(db)
    logger.info("Terminal manager initialized")

    logger.info("Initializing WebSocket handler...")
    ws_handler = TerminalWebSocketHandler(terminal_manager, db)
    logger.info("WebSocket handler initialized")

    if config.is_module_enabled("history_keeper"):
        logger.info("Initializing search engine (history_keeper enabled)...")
        search_engine = SearchEngine(db)
        logger.info("Search engine created")
        await search_engine.initialize()
        logger.info("Search engine initialized")
    else:
        logger.warning("History keeper disabled - search functionality unavailable")

    if config.is_worker_enabled("retention"):
        logger.info("Initializing retention manager...")
        retention_manager = RetentionManager(db)
        asyncio.create_task(retention_scheduler())
        logger.info("Retention manager initialized and scheduler started")
    else:
        logger.warning("Retention worker disabled")

    # Initialize capture scheduler for backend terminal capture
    if config.get("session_manager.capture.enabled", True):
        logger.info("Initializing capture scheduler...")
        global capture_scheduler
        capture_scheduler = CaptureWorkerScheduler(db)
        capture_scheduler.start()
        loop = asyncio.new_event_loop()
        loop.create_task(capture_scheduler_thread())
        threading.Thread(target=loop.run_forever, daemon=True).start()
        # asyncio.create_task(capture_scheduler_thread())
        logger.info("Capture scheduler initialized")
    else:
        logger.warning("Capture scheduler disabled")

    logger.info("flashback-terminal startup complete")
    yield

    logger.info("Shutting down flashback-terminal...")
    if capture_scheduler:
        capture_scheduler.stop()
    logger.info("Shutdown complete")


async def capture_scheduler_thread():
    """Background thread for terminal capture."""
    config = get_config()
    interval = config.get("session_manager.capture.interval_seconds", 10)

    if not capture_scheduler:
        logger.info("[capture_scheduler_thread] Capture scheduler not initialized")
        return
    if not capture_scheduler._running:
        logger.info("[capture_scheduler_thread] Capture scheduler initialized but not running")

    logger.info(f"[capture_scheduler_thread] Capture scheduler running (interval: {interval}s)")

    while capture_scheduler and capture_scheduler._running:
        logger.debug(f"[capture_scheduler_thread] Sleeping for {interval} seconds")
        await asyncio.sleep(interval)
        logger.debug(f"[capture_scheduler_thread] Waking up")
        try:
            results = await capture_scheduler.run_captures()
            if results:
                logger.debug(f"[capture_scheduler_thread] Captured {len(results)} sessions")
            else:
                logger.debug("[capture_scheduler_thread] No sessions captured")
        except Exception as e:
            logger.error(f"[capture_scheduler_thread] Capture error: {e}")
    logger.debug("[capture_scheduler_thread] Capture scheduler is down")
    


async def retention_scheduler():
    """Background task for retention management."""
    config = get_config()
    interval = config.get("workers.retention.check_interval_seconds", 3600)

    while True:
        await asyncio.sleep(interval)
        if retention_manager:
            await retention_manager.run_cleanup()


app = FastAPI(title="flashback-terminal", lifespan=lifespan,debug=logger.get_verbosity() >= Logger.DEBUG)

# Static files and templates
static_dir = os.path.join(os.path.dirname(__file__), "static")
templates_dir = os.path.join(os.path.dirname(__file__), "templates")

if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

templates = Jinja2Templates(directory=templates_dir if os.path.exists(templates_dir) else static_dir)


@app.exception_handler(Exception)
async def debug_exception_handler(request: Request, exc: Exception):
    traceback_str = traceback.format_exc()
    # Log it too
    logger.debug('[FastAPI Internal Exception] Traceback:\n'+traceback_str)

    if logger.get_verbosity() >= Logger.DEBUG:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": "Internal Server Error",
                "error": str(exc),
                "traceback": traceback_str.split("\n")  # or send as string
            },
        )
    else:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal Server Error"},
        )

@app.get("/healthcheck")
async def healthcheck():
    """Check server health"""
    return {"status": "ok"}

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Serve the main terminal UI with Jinja2 templating for verbosity."""
    config = get_config()
    verbosity = config.verbosity

    if os.environ.get('CLI_VERBOSITY'):
        verbosity = int(os.environ.get('CLI_VERBOSITY'))
        logger.debug(f"Using CLI verbosity: {verbosity}")

    logger.debug(f"Serving index page with verbosity={verbosity}")

    # Create template context
    context = {
        "request": request,  # Pass the actual request object
        "verbosity_level": verbosity,
    }

    # Try to use Jinja2 template
    template_path = os.path.join(templates_dir, "index.html")
    if os.path.exists(template_path):
        return templates.TemplateResponse(request=request, name="index.html", context=context)

    # Fallback: read static file and inject verbosity
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as f:
            content = f.read()
        # Inject verbosity before closing </head>
        verbosity_script = f"<script>window.VERBOSITY_LEVEL = {verbosity};</script>"
        content = content.replace("</head>", f"{verbosity_script}</head>")
        return content

    return "<h1>flashback-terminal</h1><p>Static files not found</p>"


# TODO: Speed this websocket terminal handler up!
@app.websocket("/ws/terminal/{session_uuid}")
async def terminal_websocket(websocket: WebSocket, session_uuid: str):
    """WebSocket endpoint for terminal sessions."""
    if ws_handler:
        # execute this corotine in separate thread
        # def handle_websocket_threaded():
        #     loop = asyncio.new_event_loop()
        #     loop.run_until_complete(ws_handler.handle(websocket, session_uuid))
        # threading.Thread(target=handle_websocket_threaded, daemon=True).start()
        await ws_handler.handle(websocket, session_uuid)


@app.post("/api/sessions/{session_uuid}/attach")
@log_function(Logger.DEBUG)
async def attach_to_session(session_uuid: str):
    """Attach to an existing terminal session."""
    logger.info(f"Attach request for session: {session_uuid}")
    
    if not terminal_manager:
        logger.error("Terminal manager not available")
        raise HTTPException(status_code=503, detail="Terminal manager not available")
    
    # Check if session is already running in terminal manager
    if session_uuid in terminal_manager.sessions:
        logger.warning(f"Session {session_uuid} is already running in terminal manager")
        raise HTTPException(status_code=409, detail="Session is already running")
    
    # Get session from database
    session = await db.get_session_by_uuid(session_uuid)
    if not session:
        logger.error(f"Session {session_uuid} not found in database")
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session.status not in ("active", "running"):
        logger.error(f"Session {session_uuid} is not active (status: {session.status})")
        raise HTTPException(status_code=400, detail="Session is not active")
    
    try:
        # Create a terminal session from existing database session
        from flashback_terminal.terminal import TerminalSession
        
        profile = get_config().get_profile(session.profile_name) or {}
        terminal_session = TerminalSession(
            session_id=session.id,
            uuid=session.uuid,
            db=db,
            profile=profile,
        )
        
        # Try to attach to existing session manager session
        # This requires the session manager to support reattaching
        session_manager = terminal_manager.session_manager
        existing_session = session_manager.get_session(session_uuid)
        
        if existing_session and await existing_session.is_running():
            # Create a new terminal session that wraps the existing backend session
            terminal_session._session = existing_session
            terminal_session._running = True
            terminal_manager.sessions[session_uuid] = terminal_session
            
            logger.info(f"Successfully attached to session {session_uuid}")
            return {
                "session_uuid": session_uuid,
                "status": "attached",
                "message": "Successfully attached to session"
            }
        else:
            logger.error(f"Session {session_uuid} backend is not running")
            raise HTTPException(status_code=400, detail="Session backend is not running")
            
    except Exception as e:
        logger.error(f"Failed to attach to session {session_uuid}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to attach to session: {str(e)}")


@app.get("/api/sessions")
@log_function(Logger.DEBUG)
async def list_sessions(
    status: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """List terminal sessions."""
    logger.debug(f"list_sessions called: status={status}, limit={limit}, offset={offset}")

    sessions = await db.list_sessions(status=status, limit=limit, offset=offset)
    
    # Check which sessions are currently running in terminal manager
    # TODO: attach to previous running sessions in db?
    running_sessions = set()
    if terminal_manager:
        running_sessions = set(terminal_manager.sessions.keys())

    logger.info(f"Listed {len(sessions)} sessions (status={status}, limit={limit})")

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
                "is_running": s.uuid in running_sessions,
                "can_attach": s.status in ("active", "running") and s.uuid not in running_sessions,
            }
            for s in sessions
        ]
    }


@app.post("/api/sessions")
@log_function(Logger.DEBUG)
async def create_session(profile: str = "default", name: Optional[str] = None):
    """Create a new terminal session."""
    logger.info(f"Creating new session: profile={profile}, name={name}")

    session = await terminal_manager.create_session(profile_name=profile, name=name)
    if not session:
        logger.error(f"Failed to create session: profile={profile}, name={name}")
        raise HTTPException(status_code=500, detail="Failed to create session")

    logger.info(f"Session created: id={session.session_id}, uuid={session.uuid}")

    return {
        "session_id": session.session_id,
        "uuid": session.uuid,
        "name": name or f"Terminal {session.session_id}",
    }


@app.get("/api/sessions/{session_uuid}")
async def get_session(session_uuid: str):
    """Get session details."""
    session = await db.get_session_by_uuid(session_uuid)
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
    session = await db.get_session_by_uuid(session_uuid)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    await db.update_session(session.id, name=name)
    return {"success": True}


@app.delete("/api/sessions/{session_uuid}")
async def delete_session(session_uuid: str):
    """Delete a session."""
    session = await db.get_session_by_uuid(session_uuid)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    await terminal_manager.close_session(session_uuid)
    await db.delete_session(session.id)
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
@log_function(Logger.DEBUG)
async def search(request: SearchRequest):
    """Search terminal history."""
    logger.info(f"Search request: query={request.query[:50]}..., mode={request.mode}, scope={request.scope}")

    if not search_engine:
        logger.error("Search not available - search_engine is None")
        raise HTTPException(status_code=503, detail="Search not available")

    target_session_ids = None
    if request.scope == "current" and request.session_ids:
        target_session_ids = []
        for uuid in request.session_ids:
            session = await db.get_session_by_uuid(uuid)
            if session:
                target_session_ids.append(session.id)
        logger.debug(f"Search limited to session_ids: {target_session_ids}")

    logger.debug("Executing search...")
    results = await search_engine.search(
        query=request.query,
        mode=request.mode,
        scope=request.scope,
        session_ids=target_session_ids,
        limit=request.limit,
        order_by=request.order_by,
        time_range=request.time_range,
        filter_inactive=request.filter_inactive,
    )

    logger.info(f"Search completed: found {len(results)} results")

    return {"query": request.query, "mode": request.mode, "results": results}


@app.get("/api/history/{session_uuid}")
async def get_history(
    session_uuid: str,
    from_seq: Optional[int] = None,
    to_seq: Optional[int] = None,
):
    """Get terminal history for a session."""
    session = await db.get_session_by_uuid(session_uuid)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    outputs = await db.get_terminal_output(session.id, from_seq, to_seq)
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
    session = await db.get_session_by_uuid(session_uuid)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    screenshots = await db.get_screenshots(session.id, limit)
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


# Timeline API endpoints
@app.get("/api/v1/captures/timeline")
@log_function(Logger.DEBUG)
async def get_captures_timeline(
    before_time: Optional[float] = None,
    around_time: Optional[float] = None,
    limit: int = Query(50, ge=1, le=200),
):
    """Get terminal captures for timeline view."""
    logger.debug(f"Timeline request: before={before_time}, around={around_time}, limit={limit}")

    captures = await db.get_terminal_captures_timeline(
        before_time=before_time,
        around_time=around_time,
        limit=limit,
    )

    # Get total count
    async with db._connect() as conn:
        total = await((await conn.execute("SELECT COUNT(*) FROM terminal_captures")).fetchone())[0]
        oldest = (await (await conn.execute(
            "SELECT MIN(strftime('%s', timestamp)) FROM terminal_captures"
        )).fetchone())[0]

    # Format results
    formatted = []
    for c in captures:
        formatted.append({
            "id": c["id"],
            "session_id": c["session_id"],
            "session_uuid": c["session_uuid"],
            "session_name": c["session_name"],
            "timestamp": c["timestamp"],
            "timestamp_formatted": c["timestamp"],
            "screenshot_url": f"/api/v1/captures/{c['id']}/screenshot" if c["screenshot_path"] else None,
            "ocr_text_preview": (c["text_content"] or "")[:200] if c["text_content"] else None,
        })

    return {
        "results": formatted,
        "total": total,
        "oldest_timestamp": float(oldest) if oldest else None,
        "time_from": captures[-1]["timestamp"] if captures else None,
        "time_to": captures[0]["timestamp"] if captures else None,
    }


@app.get("/api/v1/captures/by-id/{capture_id}")
async def get_capture_detail(capture_id: int):
    """Get detailed information about a single capture."""
    capture = await db.get_terminal_capture_by_id(capture_id)
    if not capture:
        raise HTTPException(status_code=404, detail="Capture not found")

    return {
        "id": capture["id"],
        "session_id": capture["session_id"],
        "session_uuid": capture["session_uuid"],
        "session_name": capture["session_name"],
        "timestamp": capture["timestamp"],
        "timestamp_formatted": capture["timestamp"],
        "screenshot_url": f"/api/v1/captures/{capture_id}/screenshot" if capture["screenshot_path"] else None,
        "ocr_text_full": capture["text_content"],
        "ocr_text_preview": (capture["text_content"] or "")[:200] if capture["text_content"] else None,
        "has_embedding": False,  # TODO: implement embedding check
        "window_title": capture.get("session_name", "Terminal"),
    }


@app.get("/api/v1/captures/by-id/{capture_id}/neighbors")
async def get_capture_neighbors(
    capture_id: int,
    before: int = Query(5, ge=0, le=20),
    after: int = Query(5, ge=0, le=20),
):
    """Get neighboring captures for timeline context."""
    neighbors = await db.get_terminal_capture_neighbors(capture_id, before=before, after=after)

    formatted = []
    for n in neighbors:
        # Calculate relative time in minutes
        center_time = None
        for x in neighbors:
            if x.get("is_center"):
                center_time = x["timestamp"]
                break

        rel_minutes = 0
        if center_time and not n.get("is_center"):
            # Parse timestamps and calculate difference
            try:
                from datetime import datetime
                t1 = datetime.fromisoformat(center_time.replace("Z", "+00:00"))
                t2 = datetime.fromisoformat(n["timestamp"].replace("Z", "+00:00"))
                rel_minutes = int((t2 - t1).total_seconds() / 60)
            except Exception:
                pass

        formatted.append({
            "id": n["id"],
            "timestamp": n["timestamp"],
            "timestamp_formatted": n["timestamp"],
            "screenshot_url": f"/api/v1/captures/{n['id']}/screenshot" if n["screenshot_path"] else None,
            "is_center": n.get("is_center", False),
            "relative_minutes": rel_minutes,
        })

    return {"screenshots": formatted}


@app.get("/api/v1/captures/{capture_id}/screenshot")
async def get_capture_screenshot(capture_id: int):
    """Serve a capture screenshot."""
    from fastapi.responses import FileResponse

    capture = await db.get_terminal_capture_by_id(capture_id)
    if not capture or not capture.get("screenshot_path"):
        raise HTTPException(status_code=404, detail="Screenshot not found")

    path = capture["screenshot_path"]
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Screenshot file not found")

    return FileResponse(path, media_type="image/png")


# Timeline and detail page routes
@app.get("/timeline", response_class=HTMLResponse)
async def timeline_page(request: Request):
    """Serve the timeline page."""
    return templates.TemplateResponse(request=request, name="timeline.html", context={})


@app.get("/capture/{capture_id}", response_class=HTMLResponse)
async def capture_detail_page(request: Request, capture_id: int):
    """Serve the capture detail page."""
    return templates.TemplateResponse(
        request=request,
        name="capture_detail.html",
        context={"capture_id": capture_id},
    )
