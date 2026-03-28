"""PTY terminal management for flashback-terminal."""

import fcntl
import os
import pty
import select
import signal
import struct
import termios
import uuid as uuid_mod
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from flashback_terminal.config import get_config
from flashback_terminal.database import Database


class PtySession:
    """Manages a single PTY terminal session."""

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

        self.pid: Optional[int] = None
        self.fd: Optional[int] = None
        self.sequence_num = 0
        self._cwd: Optional[str] = None
        self._running = False

    def start(self) -> bool:
        """Start the terminal session."""
        config = get_config()

        shell = self.profile.get("shell") or os.environ.get("SHELL", "/bin/bash")
        args = self.profile.get("args", [])
        cwd = Path(self.profile.get("cwd", "~")).expanduser()
        env = {**os.environ, **self.profile.get("env", {})}

        if self.profile.get("login_shell", True):
            shell_name = os.path.basename(shell)
            args = [f"-{shell_name}"] + args

        try:
            self.pid, self.fd = pty.fork()

            if self.pid == 0:
                os.chdir(cwd)
                os.execvpe(shell, [shell] + args, env)
            else:
                self._running = True
                self.resize(config.get("terminal.rows", 24), config.get("terminal.cols", 80))
                return True
        except Exception as e:
            print(f"[PtySession] Failed to start: {e}")
            return False

    def resize(self, rows: int, cols: int) -> None:
        """Resize the terminal."""
        if self.fd is not None:
            try:
                size = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(self.fd, termios.TIOCSWINSZ, size)
            except Exception as e:
                print(f"[PtySession] Resize error: {e}")

    def write(self, data: str) -> None:
        """Write data to the terminal."""
        if self.fd is not None and self._running:
            try:
                os.write(self.fd, data.encode())
            except Exception as e:
                print(f"[PtySession] Write error: {e}")

    def read(self, timeout: float = 0.1) -> Optional[str]:
        """Read data from the terminal."""
        if self.fd is None or not self._running:
            return None

        try:
            ready, _, _ = select.select([self.fd], [], [], timeout)
            if ready:
                data = os.read(self.fd, 4096)
                if data:
                    text = data.decode("utf-8", errors="replace")
                    self._log_output(text)
                    return text
                else:
                    self._running = False
        except Exception as e:
            print(f"[PtySession] Read error: {e}")
            self._running = False

        return None

    def _log_output(self, content: str) -> None:
        """Log terminal output to database."""
        config = get_config()

        if config.is_module_enabled("history_keeper"):
            self.sequence_num += 1
            self.db.insert_terminal_output(
                self.session_id, self.sequence_num, content, "output"
            )

        if self.on_output:
            self.on_output(content)

    def update_cwd(self, cwd: str) -> None:
        """Update the current working directory."""
        self._cwd = cwd
        self.db.update_session(self.session_id, last_cwd=cwd)

    def get_cwd(self) -> Optional[str]:
        """Get the current working directory."""
        return self._cwd

    def is_running(self) -> bool:
        """Check if the session is still running."""
        if self.pid and self._running:
            try:
                pid, _ = os.waitpid(self.pid, os.WNOHANG)
                if pid == 0:
                    return True
            except Exception:
                pass
        return False

    def stop(self) -> None:
        """Stop the terminal session."""
        self._running = False
        if self.pid:
            try:
                os.kill(self.pid, signal.SIGTERM)
            except Exception:
                pass
        if self.fd:
            try:
                os.close(self.fd)
            except Exception:
                pass


class TerminalManager:
    """Manages multiple terminal sessions."""

    def __init__(self, db: Database):
        self.db = db
        self.sessions: Dict[str, PtySession] = {}
        self.config = get_config()

    def create_session(
        self, profile_name: str = "default", name: Optional[str] = None
    ) -> Optional[PtySession]:
        """Create a new terminal session."""
        profile = self.config.get_profile(profile_name)
        if not profile:
            print(f"[TerminalManager] Profile not found: {profile_name}")
            return None

        uuid_str = str(uuid_mod.uuid4())
        session_name = name or f"Terminal {len(self.sessions) + 1}"

        session_id = self.db.create_session(
            uuid=uuid_str, name=session_name, profile_name=profile_name
        )

        session = PtySession(
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

    def get_session(self, uuid: str) -> Optional[PtySession]:
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
