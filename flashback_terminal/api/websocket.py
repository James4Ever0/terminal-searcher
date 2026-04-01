"""WebSocket handler for terminal sessions."""

import asyncio
import base64
import json
from io import BytesIO
from pathlib import Path
from typing import Dict

from fastapi import WebSocket, WebSocketDisconnect
from PIL import Image

from flashback_terminal.config import get_config
from flashback_terminal.database import Database
from flashback_terminal.terminal import TerminalManager, TerminalSession
from flashback_terminal.logger import logger

class TerminalWebSocketHandler:
    """Handles WebSocket connections for terminal sessions."""

    def __init__(self, terminal_manager: TerminalManager, db: Database):
        self.terminal_manager = terminal_manager
        self.db = db
        self.active_connections: Dict[str, WebSocket] = {}

    async def handle(self, websocket: WebSocket, session_uuid: str) -> None:
        """Handle a WebSocket connection."""
        # TODO: ask user to abort connection if session id already exists in active connections, in frontend.
        await websocket.accept()
        if session_uuid in self.active_connections:
            # maybe we should ask user to decide whether to abort the existing connection
            await websocket.send_json({
                "type": "session_already_exists",
                "message": "Session with this ID already exists",
            })
            # receive some message from frontend
            message = await websocket.receive_text()
            try:
                message = json.loads(message)
            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "message": "Cannot parse JSON",
                })
                await websocket.close()
                return
            logger.info(f"[WebSocket] Received message: {message}")
            if type(message) != dict:
                await websocket.send_json({
                    "type": "error",
                    "message": "JSON object is not a dictionary",
                })
                await websocket.close()
                return
            if message.get("action") == "abort":
                await websocket.close()
                return
            elif message.get("action") == "continue":
                # abort old connection
                old_websocket = self.active_connections[session_uuid]
                await old_websocket.close()
                del self.active_connections[session_uuid]
            else:
                await websocket.send_json({
                    "type": "error",
                    "message": "Invalid action",
                })
                await websocket.close()
                return

        config = get_config()

        session = self.terminal_manager.get_session(session_uuid)
        was_restored = False

        if session:
            logger.info(f"[WebSocket] Session {session_uuid} already exists")
            # print type of session.
        else:
            db_session = await self.db.get_session_by_uuid(session_uuid)
            if db_session:
                # Try to restore/reattach to the session
                logger.info(f"[WebSocket] Attempting to restore session {session_uuid}")
                session = await self.terminal_manager.restore_session(session_uuid)
                
                if session:
                    was_restored = True
                    await websocket.send_json({
                        "type": "session_restored", 
                        "message": f"Session '{db_session.name}' restored successfully",
                        "uuid": session_uuid,
                        "name": db_session.name
                    })
                else:
                    # Session exists but cannot be restored (socket not accessible)
                    await websocket.send_json({
                        "type": "session_unavailable", 
                        "message": f"Session '{db_session.name}' exists but is not accessible. The underlying terminal session may have ended.",
                        "uuid": session_uuid,
                        "name": db_session.name,
                        "can_recreate": True
                    })
                    # Create a new session instead
                    session = await self.terminal_manager.create_session()
                    if session:
                        session_uuid = session.uuid
                        await websocket.send_json({
                            "type": "session_created", 
                            "message": "Created new session instead",
                            "uuid": session_uuid
                        })
            else:
                # No existing session, create new one
                logger.info("[WebSocket] Creating new session")
                session = await self.terminal_manager.create_session()
                if session:
                    session_uuid = session.uuid

        if not session:
            logger.info("[WebSocket] Failed to create terminal session")
            await websocket.send_json(
                {"type": "error", "message": "Failed to create terminal session"}
            )
            await websocket.close()
            return

        self.active_connections[session_uuid] = websocket

        def on_output(data: str) -> None:
            logger.info(f"[WebSocket] Output event: {data}")
            asyncio.create_task(self._send_output(session_uuid, data))

        def on_clear() -> None:
            logger.info("[WebSocket] Clear event")
            asyncio.create_task(self._send_clear(session_uuid))
        
        def on_cursor(col:int, row:int) -> None:
            logger.info(f"[WebSocket] Cursor event: col={col}, row={row}")
            asyncio.create_task(self._send_cursor(session_uuid, col, row))

        logger.info(f"[WebSocket] Session type: {type(session)}")
        logger.info(f"[WebSocket] Session is running: {await session.is_running()}")

        # never made it here, maybe it is not TerminalSession?

        session.on_output = on_output
        session.on_clear = on_clear
        session.on_cursor = on_cursor

        await websocket.send_json(
            {
                "type": "session_info",
                "uuid": session_uuid,
                "name": (await self.db.get_session(session.session_id)).name if session.session_id else "Terminal",
                "restored": was_restored,  # Indicates if this was a restored session
            }
        )

        if config.is_module_enabled("session_recovery"):
            # let's not replay history? do you even know how to? why bloody unrecognized characters?
            # await self._replay_history(websocket, session.session_id)
            await self._restore_cwd(websocket, session)

        try:

            session_ready = False
            for _ in range(10):
                session_ready = await session._session._is_running()
                if session_ready: break
                else:
                    await asyncio.sleep(0.1)
            if not session_ready:
                logger.error("[WebSocket] Session is not ready after 1 second. Disconnecting.")
            # Create two asyncio looping tasks: one for terminal output, one for websocket messages
            async def read_terminal():
                while session_ready:
                    data = await session.read(timeout=0.05)
                    if not await session.is_running():
                        break
            
            async def read_websocket():
                while True:
                    try:
                        message = await websocket.receive_text()
                        await self._handle_message(websocket, session, message)
                    except WebSocketDisconnect:
                        break
                    except Exception as e:
                        logger.error(f"[WebSocket] WebSocket receive error: {e}")
                        break
            
            terminal_task = asyncio.create_task(read_terminal())
            websocket_task = asyncio.create_task(read_websocket())
            
            # Wait till one of them completes, then terminate
            done, pending = await asyncio.wait(
                [terminal_task, websocket_task],
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # Cancel any pending tasks
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        except WebSocketDisconnect:
            pass
        finally:
            if session_uuid in self.active_connections:
                del self.active_connections[session_uuid]
            session.on_output = None
            session.on_clear = None
            session.on_cursor = None
    
    async def _send_cursor(self, session_uuid:str, col:int, row:int) -> None:
        if session_uuid in self.active_connections:
            websocket = self.active_connections[session_uuid]
            await websocket.send_json({"type": "cursor", "col": col, "row": row})

    async def _send_clear(self, session_uuid: str) -> None:
        """Send clear command to the WebSocket client."""
        if session_uuid in self.active_connections:
            websocket = self.active_connections[session_uuid]
            await websocket.send_json({"type": "clear"})

    async def _send_output(self, session_uuid: str, data: str) -> None:
        """Send output to the WebSocket client."""
        if session_uuid in self.active_connections:
            websocket = self.active_connections[session_uuid]
            await websocket.send_json({"type": "output", "data": data})

    async def _handle_message(
        self, websocket: WebSocket, session: TerminalSession, message: str
    ) -> None:
        """Handle a message from the WebSocket client."""
        try:
            msg = json.loads(message)
            msg_type = msg.get("type")

            if msg_type == "input":
                await session.write(msg.get("data", ""))
            elif msg_type == "resize":
                rows = msg.get("rows", 24)
                cols = msg.get("cols", 80)
                logger.debug(f"[WebSocket] Resize request: rows={rows}, cols={cols}")
                await session.resize(rows, cols)
            elif msg_type == "command":
                cmd = msg.get("cmd")
                if cmd == "rename":
                    await session.db.update_session(session.session_id, name=msg.get("name", "Unnamed"))
                elif cmd == "set_title":
                    title = msg.get("title", "")
                    if title:
                        # Send title change to frontend for other tabs
                        await self._handle_title_change(session, title)
                        # TODO: set title in terminal using escape sequence
                        # session.write(f"\x1b]0;{title}\x07")
                elif cmd == "screenshot_upload":
                    await self._handle_screenshot_upload(session, msg)
        except json.JSONDecodeError:
            await session.write(message)

    async def _handle_title_change(
        self, session: TerminalSession, title: str
    ) -> None:
        """Handle title change and broadcast to all connected clients."""
        logger.debug(f"[WebSocket] Title change: session={session.uuid}, title={title}")
        
        # Broadcast title change to all clients connected to this session
        message = {
            "type": "title_change",
            "title": title,
            "uuid": session.uuid
        }
        
        # Send to all connected WebSocket clients
        if hasattr(self, 'connections') and session.uuid in self.connections:
            for websocket in self.connections[session.uuid]:
                try:
                    await websocket.send_text(json.dumps(message))
                except Exception as e:
                    logger.debug(f"[WebSocket] Failed to send title change: {e}")

    async def _handle_screenshot_upload(
        self, session: TerminalSession, msg: dict
    ) -> None:
        """Handle screenshot upload from the client."""
        config = get_config()
        timestamp = msg.get("timestamp")
        image_data = msg.get("data", "")

        if not image_data:
            return

        try:
            if "," in image_data:
                image_data = image_data.split(",")[1]
            img_bytes = base64.b64decode(image_data)

            img = Image.open(BytesIO(img_bytes))
            width, height = img.size

            max_size = config.get("modules.screenshot_capture.max_file_size_mb", 5)
            if len(img_bytes) > max_size * 1024 * 1024:
                return

            screenshot_dir = Path(config.screenshot_dir) / session.uuid
            screenshot_dir.mkdir(parents=True, exist_ok=True)

            filename = f"{timestamp or 'unknown'}.png"
            filepath = screenshot_dir / filename

            img.save(filepath, "PNG")

            await session.db.insert_screenshot(
                session_id=session.session_id,
                file_path=str(filepath),
                file_size=len(img_bytes),
                width=width,
                height=height,
            )

        except Exception as e:
            print(f"[WebSocket] Screenshot upload error: {e}")

    async def _replay_history(self, websocket: WebSocket, session_id: int) -> None:
        """Replay terminal history to the client."""
        config = get_config()
        max_age = config.get("modules.session_recovery.max_recovery_age_days", 30)

        session = await self.db.get_session(session_id)
        if not session:
            return

        from datetime import datetime

        age_days = (datetime.now() - session.created_at).days
        if age_days > max_age:
            return

        outputs = await self.db.get_terminal_output(session_id)
        if not outputs:
            return

        chunk_size = 100
        chunks = []
        current_chunk = []

        for output in outputs:
            current_chunk.append({"seq": output.sequence_num, "content": output.content})
            if len(current_chunk) >= chunk_size:
                chunks.append(current_chunk)
                current_chunk = []

        if current_chunk:
            chunks.append(current_chunk)

        for i, chunk in enumerate(chunks):
            await websocket.send_json(
                {
                    "type": "history_replay",
                    "chunks": chunk,
                    "is_complete": i == len(chunks) - 1,
                }
            )

    async def _restore_cwd(self, websocket: WebSocket, session: TerminalSession) -> None:
        """Restore the working directory."""
        config = get_config()
        if not config.is_module_enabled("session_recovery"):
            return

        db_session = await session.db.get_session(session.session_id)
        last_cwd = db_session.last_cwd if db_session else None

        if not last_cwd:
            return

        import os

        if os.path.isdir(last_cwd):
            await session.write(f'cd "{last_cwd}"\n')
            await websocket.send_json(
                {"type": "cwd_change", "path": last_cwd, "success": True}
            )
        else:
            await websocket.send_json(
                {
                    "type": "cwd_change",
                    "path": last_cwd,
                    "success": False,
                    "error": f"despite retries, we could not cd to {last_cwd}: No such file or directory",
                }
            )
