"""Terminal session management for flashback-terminal.

Uses GNU Screen or Tmux for session management (no local PTY mode).
This enables backend screenshot capture and text extraction.
"""

import uuid as uuid_mod
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from flashback_terminal.config import get_config
from flashback_terminal.database import Database
from flashback_terminal.logger import Logger, log_function, logger
from flashback_terminal.session_manager import (
    BaseSession,
    SessionManager,
    SessionCapture,
    get_session_manager,
)


class TerminalSession:
    """Manages a single terminal session (wrapper around BaseSession)."""

    def __init__(
        self,
        session_id: int,
        uuid: str,
        db: Database,
        profile: Dict[str, Any],
        on_output: Optional[Callable[[str], None]] = None,
        on_clear: Optional[Callable[[], None]] = None,
        on_cursor: Optional[Callable[[int, int], None]] = None,
    ):
        self.session_id = session_id
        self.uuid = uuid
        self.db = db
        self.on_output = on_output
        self.on_clear = on_clear
        self.on_cursor = on_cursor
        self.profile = profile

        self._terminal_size :Dict[str, int] = dict(rows=-1,cols=-1)

        self._session: Optional[BaseSession] = None
        self.sequence_num = 0
        self._cwd: Optional[str] = None
        self._running = False

    def _on_session_clear(self) -> None:
        """Handle clear event from underlying session."""
        if self.on_clear:
            self.on_clear()
    
    def _on_session_cursor(self, col:int, row:int) -> None:
        if self.on_cursor:
            self.on_cursor(col, row)

    async def _on_session_output(self, content: str) -> None:
        """Handle output from underlying session."""
        config = get_config()
        logger.debug(f"[TerminalSession] Received output (len={len(content)}): {content[:50]}...")

        if config.is_module_enabled("history_keeper"):
            self.sequence_num += 1
            logger.debug(f"[TerminalSession] Storing output in database (session_id={self.session_id}, seq={self.sequence_num})")
            await self.db.insert_terminal_output(
                self.session_id, self.sequence_num, content, "output"
            )

        if self.on_output:
            self.on_output(content)

    @log_function(Logger.DEBUG)
    async def start(self) -> bool:
        """Start the terminal session."""
        logger.info(f"Starting terminal session: uuid={self.uuid}, profile={self.profile.get('name', 'default')}")

        config = get_config()
        session_manager = get_session_manager()

        self._session = await session_manager.create_session(
            session_id=self.uuid,
            name=f"Terminal-{self.session_id}",
            profile=self.profile,
            on_output=self._on_session_output,
            on_clear=self._on_session_clear,
            on_cursor = self._on_session_cursor,
        )

        if self._session:
            self._running = True
            logger.info(f"Terminal session started: uuid={self.uuid}")
            return True
        else:
            logger.error(f"Failed to start terminal session: uuid={self.uuid}")
            return False

    async def resize(self, rows: int, cols: int) -> None:
        """Resize the terminal."""
        logger.debug(f"[TerminalSession] Resize request: rows={rows}, cols={cols}")
        if self._session:
            logger.debug(f"[TerminalSession] Forwarding resize to session {self._session.__class__.__name__}")
            await self._session.resize(rows, cols)
            self._terminal_size = dict(rows=rows,cols=cols)
        else:
            logger.warning(f"[TerminalSession] Cannot resize - no active session")

    async def write(self, data: str) -> None:
        """Write data to the terminal."""
        if self._session and self._running:
            await self._session.write(data)

    async def read(self, timeout: float = 0.1) -> Optional[str]:
        """Read data from the terminal."""
        if self._session is None or not self._running:
            return None

        data = await self._session.read(timeout)
        if data is None and not await self._session.is_running():
            self._running = False
        return data

    async def update_cwd(self, cwd: str) -> None:
        """Update the current working directory."""
        self._cwd = cwd
        if self._session:
            self._session.update_cwd(cwd)
        await self.db.update_session(self.session_id, last_cwd=cwd)

    def get_cwd(self) -> Optional[str]:
        """Get the current working directory."""
        if self._session:
            return self._session.get_cwd()
        return self._cwd

    async def is_running(self) -> bool:
        """Check if the session is still running."""
        if self._session and self._running:
            return await self._session.is_running()
        return False

    async def capture(self, full_scrollback: bool = False) -> Optional[SessionCapture]:
        """Capture session content (for backend screenshots)."""
        if self._session:
            return await self._session.capture(full_scrollback)
        return None

    async def stop(self) -> None:
        """Stop the terminal session."""
        self._running = False
        if self._session:
            await self._session.stop()


class TerminalManager:
    """Manages multiple terminal sessions."""

    @log_function(Logger.DEBUG)
    def __init__(self, db: Database):
        self.db = db
        self.sessions: Dict[str, TerminalSession] = {}
        self.config = get_config()
        logger.debug("TerminalManager initialized")
    
    @log_function(Logger.DEBUG)
    async def restore_session(self, session_uuid: str) -> Optional[TerminalSession]:
        """Restore a terminal session by checking socket existence first."""
        logger.info(f"Attempting to restore session: {session_uuid}")
        
        # Get session info from database
        db_session = await self.db.get_session_by_uuid(session_uuid)
        if not db_session:
            logger.warning(f"[TerminalManager] Session {session_uuid} not found in database")
            return None
        
        # Get session manager and create a temporary session to check socket existence
        session_manager = get_session_manager()
        config = get_config()
        
        # Create a temporary session object just to check if the underlying socket/session exists
        try:
            profile = self.config.get_profile(db_session.profile_name) or {"name": "default"}
            
            if db_session.session_type == "tmux":
                socket_dir = config.get("session_manager.tmux.socket_dir", "~/.flashback-terminal/tmux")
                temp_session = session_manager._sessions.get(session_uuid)
                if not temp_session:
                    temp_session = session_manager.create_session(
                        session_id=session_uuid,
                        name=f"Restore-{db_session.name}",
                        profile=profile,
                        on_output=None,
                        on_clear=None,
                        on_cursor=None
                    )
                
                # Check if the tmux session is actually running and socket exists
                if temp_session and await temp_session.is_running():
                    logger.info(f"[TerminalManager] Tmux session {session_uuid} is running and accessible")
                    
                    # Create proper TerminalSession wrapper
                    terminal_session = TerminalSession(
                        session_id=db_session.id,
                        uuid=session_uuid,
                        db=self.db,
                        profile=profile,
                    )
                    
                    # Set the underlying session reference
                    terminal_session._session = temp_session
                    terminal_session._running = True
                    
                    # Update database to reflect actual status - only after confirming socket exists
                    await self.db.update_session(db_session.id, status="active")
                    
                    # Add to active sessions
                    self.sessions[session_uuid] = terminal_session
                    
                    logger.info(f"[TerminalManager] Successfully restored session {session_uuid}")
                    return terminal_session
                else:
                    logger.warning(f"[TerminalManager] Tmux session {session_uuid} socket not accessible")
                    # Update database to reflect actual status
                    await self.db.update_session(db_session.id, status="inactive")
                    return None
                    
            elif db_session.session_type == "screen":
                socket_dir = config.get("session_manager.screen.socket_dir", "~/.flashback-terminal/screen")
                temp_session = session_manager._sessions.get(session_uuid)
                if not temp_session:
                    temp_session = session_manager.create_session(
                        session_id=session_uuid,
                        name=f"Restore-{db_session.name}",
                        profile=profile,
                        on_output=None,
                        on_clear=None,
                        on_cursor=None
                    )
                
                # Check if the screen session is actually running and socket exists
                if temp_session and await temp_session.is_running():
                    logger.info(f"[TerminalManager] Screen session {session_uuid} is running and accessible")
                    
                    # Create proper TerminalSession wrapper
                    terminal_session = TerminalSession(
                        session_id=db_session.id,
                        uuid=session_uuid,
                        db=self.db,
                        profile=profile,
                    )
                    
                    # Set the underlying session reference
                    terminal_session._session = temp_session
                    terminal_session._running = True
                    
                    # Update database to reflect actual status - only after confirming socket exists
                    await self.db.update_session(db_session.id, status="active")
                    
                    # Add to active sessions
                    self.sessions[session_uuid] = terminal_session
                    
                    logger.info(f"[TerminalManager] Successfully restored session {session_uuid}")
                    return terminal_session
                else:
                    logger.warning(f"[TerminalManager] Screen session {session_uuid} socket not accessible")
                    # Update database to reflect actual status
                    await self.db.update_session(db_session.id, status="inactive")
                    return None
                    
            else:
                logger.error(f"[TerminalManager] Unsupported session type: {db_session.session_type}")
                return None
                
        except Exception as e:
            logger.error(f"[TerminalManager] Error restoring session {session_uuid}: {e}")
            # Update database to reflect actual status
            if db_session:
                await self.db.update_session(db_session.id, status="inactive")
            return None

    @log_function(Logger.DEBUG)
    async def create_session(
        self, profile_name: str = "default", name: Optional[str] = None
    ) -> Optional[TerminalSession]:
        """Create a new terminal session."""
        logger.info(f"Creating session: profile={profile_name}, name={name}")
        profile = self.config.get_profile(profile_name)
        if not profile:
            logger.error(f"Profile not found: {profile_name}")
            return None

        uuid_str = str(uuid_mod.uuid4())
        session_name = name or f"Terminal {len(self.sessions) + 1}"

        session_id = await self.db.create_session(
            uuid=uuid_str, name=session_name, profile_name=profile_name
        )

        session = TerminalSession(
            session_id=session_id,
            uuid=uuid_str,
            db=self.db,
            profile=profile,
        )

        if await session.start():
            self.sessions[uuid_str] = session
            return session
        else:
            await self.db.delete_session(session_id)
            return None

    def get_session(self, uuid: str) -> Optional[TerminalSession]:
        """Get a session by UUID."""
        return self.sessions.get(uuid)

    async def close_session(self, uuid: str) -> None:
        """Close a session."""
        if uuid in self.sessions:
            session = self.sessions[uuid]
            await session.stop()
            await self.db.update_session(
                session.session_id,
                status="inactive",
                ended_at=datetime.now().isoformat(),
            )
            del self.sessions[uuid]

    async def capture_session(
        self,
        uuid: str,
        full_scrollback: bool = False,
    ) -> Optional[SessionCapture]:
        """Capture a session's content."""
        session = self.sessions.get(uuid)
        if session:
            return await session.capture(full_scrollback)
        return None
