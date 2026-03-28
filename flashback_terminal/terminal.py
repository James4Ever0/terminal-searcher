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
    ):
        self.session_id = session_id
        self.uuid = uuid
        self.db = db
        self.on_output = on_output
        self.profile = profile

        self._session: Optional[BaseSession] = None
        self.sequence_num = 0
        self._cwd: Optional[str] = None
        self._running = False

    def _on_session_output(self, content: str) -> None:
        """Handle output from underlying session."""
        config = get_config()

        if config.is_module_enabled("history_keeper"):
            self.sequence_num += 1
            self.db.insert_terminal_output(
                self.session_id, self.sequence_num, content, "output"
            )

        if self.on_output:
            self.on_output(content)

    @log_function(Logger.DEBUG)
    def start(self) -> bool:
        """Start the terminal session."""
        logger.info(f"Starting terminal session: uuid={self.uuid}, profile={self.profile.get('name', 'default')}")

        config = get_config()
        session_manager = SessionManager()

        self._session = session_manager.create_session(
            session_id=self.uuid,
            name=f"Terminal-{self.session_id}",
            profile=self.profile,
            on_output=self._on_session_output,
        )

        if self._session:
            self._running = True
            logger.info(f"Terminal session started: uuid={self.uuid}")
            return True
        else:
            logger.error(f"Failed to start terminal session: uuid={self.uuid}")
            return False

    def resize(self, rows: int, cols: int) -> None:
        """Resize the terminal."""
        if self._session:
            self._session.resize(rows, cols)

    def write(self, data: str) -> None:
        """Write data to the terminal."""
        if self._session and self._running:
            self._session.write(data)

    def read(self, timeout: float = 0.1) -> Optional[str]:
        """Read data from the terminal."""
        if self._session is None or not self._running:
            return None

        data = self._session.read(timeout)
        if data is None and not self._session.is_running():
            self._running = False
        return data

    def update_cwd(self, cwd: str) -> None:
        """Update the current working directory."""
        self._cwd = cwd
        if self._session:
            self._session.update_cwd(cwd)
        self.db.update_session(self.session_id, last_cwd=cwd)

    def get_cwd(self) -> Optional[str]:
        """Get the current working directory."""
        if self._session:
            return self._session.get_cwd()
        return self._cwd

    def is_running(self) -> bool:
        """Check if the session is still running."""
        if self._session and self._running:
            return self._session.is_running()
        return False

    def capture(self, full_scrollback: bool = False) -> Optional[SessionCapture]:
        """Capture session content (for backend screenshots)."""
        if self._session:
            return self._session.capture(full_scrollback)
        return None

    def stop(self) -> None:
        """Stop the terminal session."""
        self._running = False
        if self._session:
            self._session.stop()


class TerminalManager:
    """Manages multiple terminal sessions."""

    @log_function(Logger.DEBUG)
    def __init__(self, db: Database):
        self.db = db
        self.sessions: Dict[str, TerminalSession] = {}
        self.config = get_config()
        logger.debug("TerminalManager initialized")

    @log_function(Logger.DEBUG)
    def create_session(
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

        session_id = self.db.create_session(
            uuid=uuid_str, name=session_name, profile_name=profile_name
        )

        session = TerminalSession(
            session_id=session_id,
            uuid=uuid_str,
            db=self.db,
            profile=profile,
        )

        if session.start():
            self.sessions[uuid_str] = session
            return session
        else:
            self.db.delete_session(session_id)
            return None

    def get_session(self, uuid: str) -> Optional[TerminalSession]:
        """Get a session by UUID."""
        return self.sessions.get(uuid)

    def close_session(self, uuid: str) -> None:
        """Close a session."""
        if uuid in self.sessions:
            session = self.sessions[uuid]
            session.stop()
            self.db.update_session(
                session.session_id,
                status="inactive",
                ended_at=datetime.now().isoformat(),
            )
            del self.sessions[uuid]

    def capture_session(
        self,
        uuid: str,
        full_scrollback: bool = False,
    ) -> Optional[SessionCapture]:
        """Capture a session's content."""
        session = self.sessions.get(uuid)
        if session:
            return session.capture(full_scrollback)
        return None
