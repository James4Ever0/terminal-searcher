"""WebSocket handler for terminal sessions."""

import asyncio
import base64
import json
from io import BytesIO
from pathlib import Path
from typing import Dict, Optional

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
        await websocket.accept()

        config = get_config()

        session = self.terminal_manager.get_session(session_uuid)
        if not session:
            db_session = self.db.get_session_by_uuid(session_uuid)
            if db_session and db_session.status == "active":
                pass
            else:
                session = self.terminal_manager.create_session()
                if session:
                    session_uuid = session.uuid

        if not session:
            await websocket.send_json(
                {"type": "error", "message": "Failed to create terminal session"}
            )
            await websocket.close()
            return

        self.active_connections[session_uuid] = websocket

        def on_output(data: str) -> None:
            asyncio.create_task(self._send_output(session_uuid, data))

        def on_clear() -> None:
            asyncio.create_task(self._send_clear(session_uuid))
        
        def on_cursor(col:int, row:int) -> None:
            asyncio.create_task(self._send_cursor(session_uuid, col, row))

        session.on_output = on_output
        session.on_clear = on_clear
        session.on_cursor = on_cursor

        await websocket.send_json(
            {
                "type": "session_info",
                "uuid": session_uuid,
                "name": self.db.get_session(session.session_id).name if session.session_id else "Terminal",
            }
        )

        if config.is_module_enabled("session_recovery"):
            await self._replay_history(websocket, session.session_id)
            await self._restore_cwd(websocket, session)

        try:
            while True:
                # Read from terminal - on_output callback handles WebSocket send
                session.read(timeout=0.05)

                try:
                    message = await asyncio.wait_for(websocket.receive_text(), timeout=0.05)
                    await self._handle_message(websocket, session, message)
                except asyncio.TimeoutError:
                    pass

                if not session.is_running():
                    break

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
                session.write(msg.get("data", ""))
            elif msg_type == "resize":
                rows = msg.get("rows", 24)
                cols = msg.get("cols", 80)
                logger.debug(f"[WebSocket] Resize request: rows={rows}, cols={cols}")
                session.resize(rows, cols)
            elif msg_type == "command":
                cmd = msg.get("cmd")
                if cmd == "rename":
                    session.db.update_session(
                        session.session_id, name=msg.get("name", "Unnamed")
                    )
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
            session.write(message)

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

            session.db.insert_screenshot(
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

        session = self.db.get_session(session_id)
        if not session:
            return

        from datetime import datetime

        age_days = (datetime.now() - session.created_at).days
        if age_days > max_age:
            return

        outputs = self.db.get_terminal_output(session_id)
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

        db_session = session.db.get_session(session.session_id)
        last_cwd = db_session.last_cwd if db_session else None

        if not last_cwd:
            return

        import os

        if os.path.isdir(last_cwd):
            session.write(f'cd "{last_cwd}"\n')
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
