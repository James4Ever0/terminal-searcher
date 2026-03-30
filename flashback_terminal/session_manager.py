"""Session management abstraction for flashback-terminal.

Supports terminal session backends via GNU Screen or Tmux only.
Local PTY mode has been removed in favor of multiplexers for:
- Backend screenshot capture without frontend
- Better session persistence
- Server-side terminal content extraction
"""
import asyncio
import aiofiles
import pty
import fcntl
import os
import select
import shutil
import struct
import subprocess
import tempfile
import termios
import time
import uuid
import parse
import traceback
import json
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

from flashback_terminal.config import get_config
from flashback_terminal.database import Database
from flashback_terminal.logger import Logger, log_function, logger

_singleton_session_manager = None

class SessionManagerError(Exception):
    """Error raised by session manager."""
    pass


class BinaryNotFoundError(SessionManagerError):
    """Error raised when required binary is not found."""

    def __init__(self, binary: str, install_cmd: str):
        self.binary = binary
        self.install_cmd = install_cmd
        super().__init__(f"Binary '{binary}' not found in PATH")

    def __str__(self) -> str:
        return f"""
{'='*70}
BINARY NOT FOUND: {self.binary}
{'='*70}

This feature requires '{self.binary}' to be installed and in your PATH.

To install:

    {self.install_cmd}

Then ensure it's in your PATH and try again.

{'='*70}
"""


class SessionCapture:
    """Captured session content (text and/or ANSI)."""

    def __init__(
        self,
        text: Optional[str] = None,
        ansi: Optional[str] = None,
        timestamp: Optional[float] = None,
        session_name: Optional[str] = None,
    ):
        self.text = text
        self.ansi = ansi
        self.timestamp = timestamp or time.time()
        self.session_name = session_name


class SessionInfo:
    """Information about a managed session."""

    def __init__(
        self,
        session_id: str,
        name: str,
        created_at: float,
        pid: Optional[int] = None,
        socket_path: Optional[str] = None,
        is_running: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.session_id = session_id
        self.name = name
        self.created_at = created_at
        self.pid = pid
        self.socket_path = socket_path
        self.is_running = is_running
        self.metadata = metadata or {}


class BaseSession(ABC):
    """Abstract base class for terminal sessions."""

    def __init__(
        self,
        session_id: str,
        name: str,
        profile: Dict[str, Any],
        on_output: Optional[Callable[[str], Coroutine]] = None,
        on_clear: Optional[Callable[[], None]] = None,
        on_cursor: Optional[Callable[[int, int], None]] = None,
        init_commands:list[str] = []
    ):
        self.session_id = session_id
        self.name = name
        self.profile = profile
        self.on_output = on_output
        self.on_clear = on_clear
        self.on_cursor = on_cursor
        self.init_commands=init_commands
        self._sequence_num = 0
        self._cwd: Optional[str] = None
        self._created_at = time.time()
        self._terminal_size: Optional[dict[str, int]] = None
        self._is_running_cache_ttl = 5
        self._is_running_last_cache: Optional[dict] = None

    @abstractmethod
    async def start(self) -> bool:
        """Start the session."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop the session."""
        pass

    @abstractmethod
    async def write(self, data: str) -> None:
        """Write data to the session."""
        pass

    @abstractmethod
    async def read(self, timeout: float = 0.1) -> Optional[str]:
        """Read data from the session."""
        pass

    @abstractmethod
    async def resize(self, rows: int, cols: int) -> None:
        """Resize the terminal."""
        pass

    @abstractmethod
    async def capture(self, full_scrollback: bool = False) -> Optional[SessionCapture]:
        """Capture session content (for backend screenshots)."""
        pass

    def update_cwd(self, cwd: str) -> None:
        """Update current working directory."""
        self._cwd = cwd

    def get_cwd(self) -> Optional[str]:
        """Get current working directory."""
        return self._cwd

    @abstractmethod
    async def _is_running(self) -> bool:
        ...

    # def is_running(self) -> bool:
    #     return self._is_running()
    #     pass
    
    # def is_running(self)-> bool:
    # #     """Check if session is running."""
    # #     for line in traceback.format_stack():
    #         # print(line.strip())
    #     ret= self._is_running()
    #     logger.debug('[BaseSession] is_running called_time=%s, result=%s' % (time.time(), ret))
    #     breakpoint()
    #     return ret

    async def is_running(self) -> bool:
        # logger.debug("[BaseSession] Calling is_running at time: %s", time.time())
        ret = False
        cached=False
        cache_found=False
        cache_reset=False
        if self._is_running_last_cache:
            cache_found=True
            if time.time() - self._is_running_last_cache['timestamp'] < self._is_running_cache_ttl:
                cached=True
                ret = self._is_running_last_cache['is_running']
            else:
                cache_reset=True
                self._is_running_last_cache = None
        if not self._is_running_last_cache:
            is_running = await self._is_running()
            ret = is_running
            if is_running:
                self._is_running_last_cache = dict(timestamp = time.time(), is_running = is_running)
        logger.trace('[BaseSession] is_running cache_used=%s, cache_found=%s, called_time=%s, result=%s, cache_reset=%s' % (cached,cache_found, time.time(), ret, cache_reset))
        return ret

    async def _log_output(self, content: str) -> None:
        """Log output for history keeper."""
        self._sequence_num += 1
        logger.debug(f"[{self.__class__.__name__}] Logging output (seq={self._sequence_num}, len={len(content)}): {content[:50]}...")
        if self.on_output:
            await self.on_output(content)


class TmuxSession(BaseSession):
    """Tmux-based session management."""

    def __init__(
        self,
        session_id: str,
        name: str,
        profile: Dict[str, Any],
        socket_dir: str,
        on_output: Optional[Callable[[str], Coroutine]] = None,
        on_clear: Optional[Callable[[], None]] = None,
        on_cursor: Optional[Callable[[int, int], None]] = None,
        init_commands:list[str] = [],
    ):
        super().__init__(session_id, name, profile, on_output, on_clear, on_cursor, init_commands=init_commands)
        self._socket_dir = Path(socket_dir).expanduser()
        self._socket_name = f"flashback-{self.session_id}"
        self._socket_path = self._socket_dir / self._socket_name
        self._tmux_binary = "tmux"
        self._target = f"{self._socket_name}:0.0"
        self._config_file: Optional[str] = None
        self._running = False
        self._pty_fd: Optional[int] = None
        self.pid: Optional[int] = None
        self._last_output: Optional[str] = None
        self._kiosk_config = """
unbind-key 'C-b'

set -g status off
set -g mouse off

set -g default-terminal "screen-256color"
"""

    def _get_env(self) -> Dict[str, str]:
        """Get environment for tmux commands (unsets TMUX for nested sessions)."""
        env = {**os.environ}
        # Unset tmux-related environment variables for nested session support
        tmux_vars = [
            "TMUX", "TMUX_PANE", "TMUX_WINDOW", "TMUX_SESSION",
            "TMUXinator_CONFIG", "TMUXINATOR_CONFIG"
        ]
        for var in tmux_vars:
            env.pop(var, None)
        # Set custom socket
        env['TERM'] = 'xterm-256color'
        env["TMUX_TMPDIR"] = str(self._socket_dir)
        if self._terminal_size:
            env['LINES'] = str(self._terminal_size['rows'])
            env["COLUMNS"] = str(self._terminal_size['cols'])
        return env

    def _get_attach_tty(self):
        try:
            self.pid, self._pty_fd = pty.fork()
            args = [ '-S', str(self._socket_path), 'attach']
            logger.debug(f"[TmuxSession] Child process execvpe: tmux {' '.join(args)}")
            if self.pid == 0:
                # self._running = False
                # self._pty_fd = None
                # ATTENTION: must pass os.environ. is there anything special about the environ? can we pass empty dict to it?
                # cannot pass empty dict, otherwise we will fail to obtain output or spawn process
                os.execvpe(shutil.which("tmux"), [shutil.which("tmux")] + args, dict(SHELL='/bin/bash', TERM='xterm-256color', LANG='en_US.UTF-8'))
            else:
                logger.debug(f"[TmuxSession] Attached to tmux session at {self._socket_path}")
                self._running = True
                return True
        except Exception as e:
            logger.debug(f"[TmuxSession] Failed to attach: {e}")
            return False

    async def _get_pane_tty(self) -> Optional[str]:
        """Get the pty device path for the tmux pane."""
        try:
            result = await self._run_tmux([
                "display-message",
                "-p",
                "-t", self._target,
                "#{pane_tty}",
            ], check=False, get_output=True)
            if result.returncode == 0 and result.stdout:
                return result.stdout.strip()
        except Exception as e:
            logger.debug(f"Failed to get pane tty: {e}")
        return None

    async def _run_tmux(self, args: List[str], check: bool = True, get_output:bool=False, input=None) -> subprocess.CompletedProcess:
        """Run tmux command with custom socket."""
        cmd = [
            self._tmux_binary,
            "-S", str(self._socket_path),
            "-T", "mouse,256,focus,title"
        ]
        if self._config_file:
            cmd.extend(["-f", self._config_file])
        else:
            # Add kiosk config
            self._config_file = self._socket_dir / f"{self._socket_name}.conf"
            self._config_file.write_text(self._kiosk_config)
            cmd.extend(["-f", str(self._config_file)])
        cmd.extend(args)

        env = self._get_env()
        
        logger.trace("[TmuxSession] Tmux execution cmd: {}".format(" ".join([str(it) for it in cmd])))
        # Use asyncio subprocess for async execution
        # it must stucked at here?
        if get_output:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await process.communicate(input)
            logger.debug("[TmuxSession] Process stdout:\n%s"%stdout)
            logger.debug("[TmuxSession] Process stderr:\n%s"%stderr)
        else:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
            )
            await process.wait()
            stdout, stderr = b"", b""
        logger.debug("Process return code: %s" % process.returncode)
        
        result = subprocess.CompletedProcess(
            args=cmd,
            returncode=process.returncode,
            stdout=stdout.decode('utf-8', errors='replace'),
            stderr=stderr.decode('utf-8', errors='replace')
        )

        if check and result.returncode != 0:
            raise SessionManagerError(
                f"tmux command failed: {' '.join(args)}\n{result.stderr}"
            )
        return result

    @log_function(Logger.DEBUG)
    async def start(self) -> bool:
        """Start tmux session."""
        logger.info(f"Starting tmux session: {self.session_id}")

        self._socket_dir.mkdir(parents=True, exist_ok=True)

        shell = self.profile.get("shell") or os.environ.get("SHELL", "/bin/bash")
        args = self.profile.get("args", [])
        cwd = Path(self.profile.get("cwd", "~")).expanduser()
        profile_env = self.profile.get("env", {})

        if self.profile.get("login_shell", True):
            shell_name = os.path.basename(shell)
            args = [f"-{shell_name}"] + args

        # Build command to start shell
        start_command = f"cd {cwd} && exec {' '.join([shell] + args)}"

        try:
            # Check if we are "attaching" to an existing session
            if await self.is_running():
                logger.debug("[TmuxSession] Attaching to existing session")
            else:
                # Create new session detached
                await self._run_tmux([
                    "new-session",
                    "-d",
                    "-s", self._socket_name,
                    "-n", "main",
                    "-e", "TERM=xterm-256color",
                    start_command,
                ])

                await self._run_tmux(["set-option", '-g', "default-terminal", "xterm-256color"])

                # set -g status off
                # set -g mouse off
                await self._run_tmux(["set-option", "-g", "status", "off"])
                await self._run_tmux(["set-option", "-g", "mouse", "off"])

                # set-environment -r TMUX
                await self._run_tmux(["set-environment", "-r", "TMUX"])

                # unbind "Ctrl b" normal
                await self._run_tmux(["unbind-key", "C-b"])

                # unbind-key -a
                await self._run_tmux(["unbind-key", "-a"])

                # Set environment variables
                for key, value in profile_env.items():
                    await self._run_tmux([
                        "set-environment",
                        "-t", self._socket_name,
                        key, value,
                    ], check=False)

            self._running = True

            # Attach to tmux session using a pty for direct I/O
            # Wait for pane to be ready before attaching
            has_pane_tty_path = False
            for _ in range(5):
                pane_tty_path = await self._get_pane_tty()
                if pane_tty_path and os.path.exists(pane_tty_path):
                    has_pane_tty_path = True
                    break
                await asyncio.sleep(0.1)

            if has_pane_tty_path:
                # Fork a pty and run tmux attach to connect to the session
                if self._get_attach_tty():
                    logger.debug(f"Attached to tmux session {self._socket_name} via pty")
                else:
                    logger.warning(f"Failed to attach to tmux session {self._socket_name}, using capture-pane fallback")
                    self._pty_fd = None
            else:
                logger.warning(f"Could not find pty device for tmux session {self._socket_name}, using capture-pane fallback")

            logger.info(f"[TmuxSession] Tmux session started: {self._socket_name}")
            # execute init commands
            logger.info(f"[TmuxSession] Executing init commands: {self.init_commands}")
            for cmd in self.init_commands:
                escaped = cmd.replace('"', '\\"')
                await self._run_tmux([
                    "send-keys",
                    "-t", self._target,
                    escaped,
                ], check=False)
                await self._run_tmux([
                    "send-keys",
                    "-t", self._target,
                    "Enter",
                ], check=False)
            return True

        except Exception as e:
            import traceback
            logger.error(traceback.format_exc())
            logger.error(f"[TmuxSession] Failed to start tmux session: {e}")
            return False

    async def stop(self) -> None:
        """Stop tmux session."""
        # Close pty if open
        if self._pty_fd is not None:
            try:
                os.close(self._pty_fd)
            except Exception:
                pass
            self._pty_fd = None

        try:
            await self._run_tmux([
                "kill-session",
                "-t", self._socket_name,
            ], check=False)
        except Exception as e:
            logger.debug(f"[TmuxSession] Error stopping tmux session: {e}")
        self._running = False

    async def write(self, data: str) -> None:
        """Send keys to tmux session."""
        if not self._running:
            logger.debug(f"[TmuxSession] Session not running, cannot write")
            return

        # Try pty first if available
        if self._pty_fd is not None:
            logger.debug(f"[TmuxSession] Writing to pty for session {self._socket_name}")
            try:
                # Write to pty device asynchronously
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, os.write, self._pty_fd, data.encode())
                return
            except (OSError, BlockingIOError) as e:
                logger.debug(f"[TmuxSession] Pty write failed, falling back to send-keys: {e}")
                # Fall back to send-keys
        else:
            logger.debug(f"[TmuxSession] No pty available for tmux session {self._socket_name}, cannot use pty write.")

        try:
            # Escape special characters for tmux send-keys
            escaped = data.replace("'", "'\"'\"'")
            await self._run_tmux([
                "send-keys",
                "-t", self._target,
                escaped,
            ], check=False)
        except Exception as e:
            logger.debug(f"[TmuxSession] Write error: {e}")

    async def read(self, timeout: float = 0.1) -> Optional[str]:
        """Read from tmux session (via pty or capture-pane)."""
        if not self._running:
            logger.debug(f"[TmuxSession] Session {self._socket_name} not running, cannot read")
            return None

        # # Try pty first if available
        if self._pty_fd is None:
            logger.debug(f"[TmuxSession] No pty available for tmux session {self._socket_name}, cannot use pty read.")
        else:
            try:
                # Use asyncio to wait for data to be available
                loop = asyncio.get_event_loop()
                ready = await loop.run_in_executor(None, select.select, [self._pty_fd], [], [], timeout)
                if not ready[0]:
                    # No data available
                    return None
                data = await loop.run_in_executor(None, os.read, self._pty_fd, 4096)
                if data:
                    logger.debug(f"[TmuxSession] Read {len(data)} bytes from pty")
                    text = data.decode("utf-8", errors="replace")
                    await self._log_output(text)
                    self._read_mode = "pty"
                    return text
                else:
                    # EOF - pty closed
                    logger.debug(f"[TmuxSession] Pty closed")
                    self._pty_fd = None
                    return None
            except (OSError, BlockingIOError) as e:
                logger.debug(f"[TmuxSession] Pty read failed, falling back to capture-pane: {e}")
                # Fall back to capture-pane

        try:
            # Use capture-pane with -S - to get only the bottom line (newest content)
            # This avoids capturing the entire screen content every time
            result = await self._run_tmux([
                "capture-pane",
                "-p",  # Print to stdout
                "-J",  # Join wrapped lines
                "-t", self._target,
            ], check=False, get_output=True)

            if result.returncode == 0 and result.stdout:
                text = result.stdout
                if text == self._last_output:
                    return None
                
                self._last_output = text
                self.on_clear()
                if self._last_output:
                    # Calculate what's new by comparing with last output
                    if text.startswith(self._last_output):
                        new_content = text[len(self._last_output):]
                        if new_content.strip():  # Only log if there's actual new content
                            await self._log_output(new_content)
                    else:
                        # Content changed significantly, log everything
                        await self._log_output(text)
                col, row = self._get_cursor()
                self.on_cursor(col, row)
                self._read_mode = "capture_pane"
                return text
        except Exception as e:
            traceback.print_exc()
            logger.debug(f"[TmuxSession] Read error: {e}")

        return None
    
    def _get_cursor(self) -> (int, int):
        has_cursor, (col, row) = self._get_cursor_coordinates()
        if has_cursor:
            return col, row
        return 0, 0

    def _get_info(self):
        list_session_format_template = "[#{session_name}] socket: #{socket_path} size: #{window_width}x#{window_height} cursor at: x=#{cursor_x},y=#{cursor_y} cursor flag: #{cursor_flag} cursor character: #{cursor_character} insert flag: #{insert_flag}, keypad cursor flag: #{keypad_cursor_flag}, keypad flag: #{keypad_flag}"
        # session_filter = "#{==:#{session_name}," + self.name + "}"
        output_bytes = subprocess.check_output(
            ["tmux", "-S", str(self._socket_path), "list-sessions", "-F", list_session_format_template, 
            # "-f", session_filter
            ]
        )
        logger.debug("[*] Output bytes:\n" + output_bytes.decode("utf-8"))
        numeric_properties = [
            "window_width",
            "window_height",
            "cursor_x",
            "cursor_y",
            "cursor_flag",
            "insert_flag",
            "keypad_cursor_flag",
            "keypad_flag",
        ]
        # nonword_properties = ['cursor_character', 'socket_path']
        parse_format = list_session_format_template.replace(
            "#{", "{"
        )  # .replace("}",":w}")
        # for it in nonword_properties:
        #     parse_format = parse_format.replace("{"+it+":w}","{"+it+"}")
        output = output_bytes.decode('utf-8', errors="strict")
        output = output[:-1]  # strip trailing newline
        # print("[*] Parse format:")
        # print(parse_format)
        data = parse.parse(parse_format, output)
        if isinstance(data, parse.Result):
            ret = data.named
            for it in numeric_properties:
                ret[it] = int(ret[it])  # type: ignore
            pprint_result = json.dumps(ret, indent=4, ensure_ascii=False)
            logger.debug("[+] Fetched info for session:"+ str(self._socket_path)+"\n"+pprint_result)
            return ret
        else:
            print("[-] No info for session:", self.name)


    def _get_cursor_coordinates(self):
        print("[*] Requesting cursor coordinates")
        has_cursor = False
        coordinates = (-1, -1)
        info = self._get_info()
        if info is None:
            print("[-] Failed to fetch corsor coordinates")
        else:
            x, y = info["cursor_x"], info["cursor_y"]
            print("[*] Cursor at: %d, %d" % (x, y))
            coordinates = (x, y)
            has_cursor = True
        return has_cursor, coordinates

    async def resize(self, rows: int, cols: int) -> None:
        """Resize tmux window."""
        self._terminal_size = dict(rows=rows, cols=cols)
        # Try to resize pty directly
        if self._pty_fd is not None:
            try:
                size = struct.pack("HHHH", rows, cols, 0, 0)
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, fcntl.ioctl, self._pty_fd, termios.TIOCSWINSZ, size)
                return
            except Exception as e:
                logger.debug(f"Pty resize failed, falling back to tmux: {e}")

        try:
            await self._run_tmux([
                "resize-window",
                "-t", self._target,
                "-x", str(cols),
                "-y", str(rows),
            ], check=False)
        except Exception as e:
            logger.debug(f"Resize error: {e}")


    async def _is_running(self) -> bool:
        """Check if tmux session is running and socket exists."""
        try:
            # First check if tmux reports the session as running
            result = await self._run_tmux([
                "has-session",
                "-t", self._socket_name,
            ], check=False)
            if result.returncode != 0:
                logger.debug(f"[TmuxSession] Session {self._socket_name} not found by tmux")
                return False
            
            # Then verify the socket actually exists on filesystem
            socket_path = Path(self._socket_path)
            if not socket_path.exists():
                logger.debug(f"[TmuxSession] Socket file {socket_path} does not exist")
                return False
            
            # Additional check: verify we can get basic session info
            # This ensures the socket is actually functional
            try:
                info_result = await self._run_tmux([
                    "display-message",
                    "-p", 
                    "-t", self._target,
                    "#{session_id}"
                ], check=False)
                if info_result.returncode != 0:
                    logger.debug(f"[TmuxSession] Socket exists but session is not accessible")
                    return False
            except Exception as e:
                logger.debug(f"[TmuxSession] Failed to verify session accessibility: {e}")
                return False
            
            logger.debug(f"[TmuxSession] Session {self._socket_name} is running and socket is accessible")
            return True
        except Exception as e:
            traceback.print_exc()
            logger.debug(f"[TmuxSession] Error checking session status: {e}")
            return False

    async def capture(self, full_scrollback: bool = False) -> Optional[SessionCapture]:
        """Capture tmux pane content."""
        try:
            # Capture with escape sequences (ANSI)
            ansi_args = ["capture-pane", "-p", "-e", "-t", self._target]
            if full_scrollback:
                ansi_args.extend(["-S", "-", "-E", "-"])
            ansi_result = await self._run_tmux(ansi_args, check=False, get_output=True)

            # Capture plain text
            text_args = ["capture-pane", "-p", "-t", self._target]
            if full_scrollback:
                text_args.extend(["-S", "-", "-E", "-"])
            text_result = await self._run_tmux(text_args, check=False, get_output=True)

            if not text_result.stdout:
                logger.warning("[TmuxSession] No text result in capture for session: %s" % self._socket_path)
            if not ansi_result.stdout:
                logger.warning("[TmuxSession] No ansi result in capture for session: %s" % self._socket_path)

            return SessionCapture(
                text=text_result.stdout if text_result.returncode == 0 else None,
                ansi=ansi_result.stdout if ansi_result.returncode == 0 else None,
                session_name=self._socket_name,
            )
        except Exception as e:
            logger.error(f"[TmuxSession] Capture error: {e}")
            return None


class ScreenSession(BaseSession):
    """GNU Screen-based session management."""

    def __init__(
        self,
        session_id: str,
        name: str,
        profile: Dict[str, Any],
        socket_dir: str,
        on_output: Optional[Callable[[str], Coroutine]] = None,
        init_commands: list[str] = [],
    ):
        super().__init__(session_id, name, profile, on_output, init_commands=init_commands)
        self._kiosk_config = """
# Disable all command keys
escape ''
unbindall

# Remove status/info displays
hardstatus off
startup_message off
vbell off

# Remove STY variable from new windows
unsetenv STY

# Optional: Set a restricted shell as default
# shell /bin/rbash
"""
        self._socket_dir = Path(socket_dir).expanduser()
        self._session_name = f"flashback-{self.session_id}"
        self._socket_path = self._socket_dir / self._session_name
        self._screen_binary = "screen"
        self._running = False
        self._config_file: Optional[str] = None
        self._pty_fd: Optional[int] = None
        self._read_mode: Optional[str] = None
        self.pid: Optional[int] = None

    async def _run_screen(self, args: List[str], check: bool = True, get_output:bool=False, input=None) -> subprocess.CompletedProcess:
        """Run screen command with custom socket."""
        cmd = [
            self._screen_binary,
            "-S", self._session_name,
        ]
        cmd.extend(args)

        # Create custom environment with SCREENDIR for socket location
        env = {**os.environ}
        env["SCREENDIR"] = str(self._socket_dir)

        if self._terminal_size:
            env['LINES'] = str(self._terminal_size['rows'])
            env["COLUMNS"] = str(self._terminal_size['cols'])
            
        env.pop("STY", None)  # Unset STY for nested session support

        logger.trace("[ScreenSession] Running screen command with SCREENDIR=%s: %s" % (self._socket_dir, " ".join(cmd)))

        # Use asyncio subprocess for async execution

        if get_output:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await process.communicate(input)
            logger.debug("[ScreenSession] Process stdout:\n%s"%stdout)
            logger.debug("[ScreenSession] Process stderr:\n%s"%stderr)
        else:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
            )
            await process.wait()
            stdout, stderr = b"", b""
        logger.debug("[ScreenSession] Process return code: %s" % process.returncode)
        
        result = subprocess.CompletedProcess(
            args=cmd,
            returncode=process.returncode,
            stdout=stdout.decode('utf-8', errors='replace'),
            stderr=stderr.decode('utf-8', errors='replace')
        )

        if check and result.returncode != 0:
            raise SessionManagerError(
                f"screen command failed: {' '.join(args)}\nRaw cmd: {' '.join(cmd)}\nStdout:{result.stdout}\nStderr:{result.stderr}"
            )
        return result



    def _get_attach_tty(self):
        try:
            self.pid, self._pty_fd = pty.fork()
            args = ['-S', str(self._session_name), '-A', '-r', '-O', '-a']
            logger.debug(f"[ScreenSession] Child process execvpe: screen {' '.join(args)}")
            if self.pid == 0:
                # self._running = False
                # self._pty_fd = None
                # ATTENTION: must pass os.environ. is there anything special about the environ? can we pass empty dict to it?
                # cannot pass empty dict, otherwise we will fail to obtain output or spawn process
                os.execvpe(shutil.which("screen"), [shutil.which("screen")] + args, dict(SHELL='/bin/bash', TERM='xterm-256color', LANG='en_US.UTF-8', SCREENDIR=self._socket_dir))
            else:
                logger.debug(f"[ScreenSession] Attached to screen session at {self._socket_path}")
                self._running = True
                return True
        except Exception as e:
            logger.debug(f"[ScreenSession] Failed to attach: {e}")
            return False

    async def _get_screen_pty(self) -> Optional[str]:
        """Get the pty device path for the screen session."""
        try:
            # Get screen session info
            result = await self._run_screen(["-list"], check=False, get_output=True)
            if result.returncode != 0:
                return None
            # Parse output to find our session
            # Example: "\t1234.flashback-session_id\t(Detached)"
            lines = result.stdout.splitlines()
            logger.debug("[ScreenSession] Screen list output:\n" + result.stdout)
            for line in lines:
                if self._session_name in line:
                    # Extract pid (first token before dot)
                    parts = line.strip().split()
                    if not parts:
                        logger.debug("[ScreenSession] Cannot found part with session name: " + self._session_name)
                    else:
                        pid_dot = parts[0]
                        logger.debug("[ScreenSession] Found socket name: " +pid_dot)
                        if '.' in pid_dot:
                            pid = pid_dot.split('.')[0]
                            # Find pty in /proc/<pid>/fd
                            proc_fd_dir = Path(f"/proc/{pid}/fd")
                            socket_path = self._socket_dir / pid_dot
                            if proc_fd_dir.exists():
                                logger.debug("[ScreenSession] Socket is running: %s" % socket_path)
                                return socket_path
                            else:
                                logger.debug("[ScreenSession] PID %s not running for socket %s" % (pid, socket_path))
        except Exception as e:
            logger.debug(f"[ScreenSession] Failed to get screen pty: {e}")
        return None

    @log_function(Logger.DEBUG)
    async def start(self) -> bool:
        """Start screen session."""
        logger.info(f"Starting screen session: {self.session_id}")

        self._socket_dir.mkdir(parents=True, exist_ok=True)
        # change mode to 700
        self._socket_dir.chmod(0o700)

        shell = self.profile.get("shell") or os.environ.get("SHELL", "/bin/bash")
        args = self.profile.get("args", [])
        cwd = Path(self.profile.get("cwd", "~")).expanduser()
        profile_env = self.profile.get("env", {})

        if self.profile.get("login_shell", True):
            shell_name = os.path.basename(shell)
            args = [f"-{shell_name}"] + args

        # Build environment setup
        env_setup = ""
        for key, value in profile_env.items():
            env_setup += f'export {key}="{value}"; '

        start_command = f"cd {cwd} && {env_setup}exec {' '.join([shell] + args)}"

        try:
            # Build screen command
            screen_cmd = [
                "-T", "xterm-256color", # more colors
                "-a",
                "-O", # optimized
                "-U", # Unicode
                "-d", "-m",  # Detached mode
                "-s", shell,
            ]

            # Add custom config if provided
            if self._config_file:
                screen_cmd.extend(["-c", self._config_file])
            else:
                # Add kiosk config
                self._config_file = self._socket_dir / f"{self._session_name}_config.rc"
                self._config_file.write_text(self._kiosk_config)
                screen_cmd.extend(["-c", str(self._config_file)])

            screen_cmd.extend(["bash", "-c", start_command])

            if not await self.is_running():
                await self._run_screen(screen_cmd)
            else:
                logger.debug("[ScreenSession] Attaching to existing session")

            self._running = True

            # Try to open the session's pty device for direct I/O
            raw_pty_path = None
            for _ in range(5):
                raw_pty_path = await self._get_screen_pty()
                if raw_pty_path and os.path.exists(raw_pty_path):
                    break
                await asyncio.sleep(0.1)
            logger.debug("[ScreenSession] Session Path: %s | Raw PTY path: %s" % (self._session_name, raw_pty_path))


            if raw_pty_path and os.path.exists(raw_pty_path):
                try:
                    self._get_attach_tty()
                    logger.debug(f"Attached to pty {raw_pty_path} for screen session {self._session_name}")
                except Exception as e:
                    logger.warning(f"Failed to attach to pty {raw_pty_path}: {e}")
                    self._pty_fd = None
            else:
                logger.warning(f"Could not find pty device for screen session {self._session_name}, using stuff/hardcopy fallback")

            logger.info(f"[ScreenSession] Screen session started: {self._session_name}")

            logger.info(f"[ScreenSession] Executing init commands: {self.init_commands}")
            for cmd in self.init_commands:
                escaped = cmd.replace("'", "'\"'\"'")
                await self._run_screen([
                    "-X", "stuff", escaped,
                ], check=False)
                await self._run_screen([
                    "-X", "stuff", "\n",
                ], check=False)
            return True

        except Exception as e:
            logger.error(f"[ScreenSession] Failed to start screen session: {e}")
            return False

    async def stop(self) -> None:
        """Stop screen session."""
        # Close pty if open
        if self._pty_fd is not None:
            try:
                os.close(self._pty_fd)
            except Exception:
                pass
            self._pty_fd = None

        try:
            await self._run_screen([
                "-X", "quit",
            ], check=False)
        except Exception as e:
            logger.debug(f"[ScreenSession] Error stopping screen session: {e}")
        self._running = False

    async def write(self, data: str) -> None:
        """Send input to screen session."""
        if not self._running:
            return

        # Try pty first if available
        if self._pty_fd is not None:
            try:
                # Write to pty device asynchronously
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, os.write, self._pty_fd, data.encode())
                return
            except (OSError, BlockingIOError) as e:
                logger.debug(f"[ScreenSession] Pty write failed, falling back to stuff: {e}")
                # Fall back to stuff

        try:
            # Use screen's stuff command to send input
            escaped = data.replace("'", "'\"'\"'")
            await self._run_screen([
                "-X", "stuff", escaped,
            ], check=False)
        except Exception as e:
            logger.debug(f"[ScreenSession] Write error: {e}")

    async def read(self, timeout: float = 0.1) -> Optional[str]:
        """Read from screen session (via pty or not supported)."""
        if not self._running:
            return None

        # Try pty first if available
        if self._pty_fd is not None:
            try:
                # Use asyncio to wait for data to be available
                loop = asyncio.get_event_loop()
                ready = await loop.run_in_executor(None, select.select, [self._pty_fd], [], [], timeout)
                if ready[0]:
                    data = await loop.run_in_executor(None, os.read, self._pty_fd, 4096)
                    if data:
                        text = data.decode("utf-8", errors="replace")
                        await self._log_output(text)
                        return text
                return None
            except (OSError, BlockingIOError) as e:
                logger.debug(f"[ScreenSession] Pty read failed: {e}")
                return None

        # Screen doesn't have a direct read mechanism like PTY
        # We use hardcopy for capture instead
        return None

    async def resize(self, rows: int, cols: int) -> None:
        """Resize screen window."""
        self._terminal_size = dict(rows=rows, cols=cols)

        # TODO: row wise resize is not good for gnu screen. also the row position is not good when we attach to it from the web interface.
        
        # Try to resize pty directly
        if self._pty_fd is not None:
            try:
                size = struct.pack("HHHH", rows, cols, 0, 0)
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, fcntl.ioctl, self._pty_fd, termios.TIOCSWINSZ, size)

                await self._run_screen([
                    "-X", "fit"
                ], check=False)
                return
            except Exception as e:
                logger.debug(f"[ScreenSession] Pty resize failed, falling back to screen resize: {e}")

        # Fallback to screen resize commands
        try:
            await self._run_screen([
                "-X", "fit"
            ], check=False)
            
            logger.debug(f"[ScreenSession] Resized to {rows}x{cols} using screen commands")
        except Exception as e:
            logger.debug(f"[ScreenSession] Screen resize error: {e}")


    async def _is_running(self) -> bool:
        """Check if screen session is running and socket exists."""
        try:
            # First check if screen reports the session as running
            result = await self._run_screen([
                "-ls",
            ], check=False, get_output=True)
            # Check if our session name appears in the output
            if not (self._session_name in result.stdout or str(self._socket_path) in result.stdout):
                logger.debug(f"[ScreenSession] Session {self._session_name} not found by screen")
                return False
            
            # Then verify the socket directory and socket file actually exist
            socket_dir = self._socket_dir
            if not socket_dir.exists():
                logger.debug(f"[ScreenSession] Socket directory {socket_dir} does not exist")
                return False
            
            # For screen, we need to check for the socket file pattern
            # Screen creates socket files in SCREENDIR with pattern: pid.sessionname
            try:
                import glob
                socket_pattern = str(socket_dir / f"*.{self._session_name}")
                socket_files = glob.glob(socket_pattern)
                
                if not socket_files:
                    logger.debug(f"[ScreenSession] No socket files found for session {self._session_name}")
                    return False
                
                # Check if any of the socket files are accessible
                for socket_file in socket_files:
                    socket_path = Path(socket_file)
                    if socket_path.exists() and socket_path.is_socket():
                        # Extract PID from socket filename and verify process is running
                        pid_str = socket_path.name.split('.')[0]
                        try:
                            pid = int(pid_str)
                            proc_path = Path(f"/proc/{pid}")
                            if proc_path.exists():
                                logger.debug(f"[ScreenSession] Session {self._session_name} is running with PID {pid}")
                                return True
                        except (ValueError, FileNotFoundError):
                            continue
                
                logger.debug(f"[ScreenSession] Socket files found but no running process detected")
                return False
                
            except Exception as e:
                logger.debug(f"[ScreenSession] Error checking socket files: {e}")
                return False
            
        except Exception as e:
            traceback.print_exc()
            logger.debug(f"[ScreenSession] Error checking session status: {e}")
            return False

    async def capture(self, full_scrollback: bool = False) -> Optional[SessionCapture]:
        """Capture screen session content using hardcopy."""
        try:
            # Create temp file for hardcopy
            with tempfile.NamedTemporaryFile(mode='w+', suffix='.txt', delete=True) as f:
                temp_path = f.name

                # Use hardcopy to dump screen content
                # -h flag for hardcopy (dump scrollback buffer)
                hardcopy_args = ["-X", "hardcopy"]
                if full_scrollback:
                    hardcopy_args.append("-h")
                hardcopy_args.append(temp_path)

                await self._run_screen(hardcopy_args, check=False, get_output=True)

                # Read the captured content asynchronously
                await asyncio.sleep(0.1)  # Give screen time to write
                async with aiofiles.open(temp_path, 'r', encoding='utf-8', errors='replace') as f:
                    content = await f.read()
                
                if not content:
                    logger.warning("[ScreenSession] No ansi result in capture for session: %s" % self._socket_path)

                return SessionCapture(
                    text=content,
                    ansi=None,  # Screen hardcopy doesn't preserve ANSI
                    session_name=self._session_name,
                )
        except Exception as e:
            logger.error(f"[ScreenSession] Capture error: {e}")
            return None


class SessionManager:
    """Factory and manager for terminal sessions (screen/tmux only)."""

    def __init__(self):
        self.config = get_config()
        self._sessions: Dict[str, BaseSession] = {}
        self._check_dependencies()

    def _check_dependencies(self) -> None:
        """Check if required binaries are in PATH."""
        mode = self.config.session_manager_mode

        if mode == "tmux":
            binary = self.config.get("session_manager.tmux.binary", "tmux")
            if not shutil.which(binary):
                raise BinaryNotFoundError(
                    binary,
                    "sudo apt-get install tmux  # Debian/Ubuntu\n"
                    "sudo yum install tmux      # RHEL/CentOS\n"
                    "brew install tmux          # macOS\n"
                    "pacman -S tmux             # Arch Linux"
                )

        elif mode == "screen":
            binary = self.config.get("session_manager.screen.binary", "screen")
            if not shutil.which(binary):
                raise BinaryNotFoundError(
                    binary,
                    "sudo apt-get install screen  # Debian/Ubuntu\n"
                    "sudo yum install screen      # RHEL/CentOS\n"
                    "brew install screen          # macOS\n"
                    "pacman -S screen             # Arch Linux"
                )
        else:
            # Default to tmux if mode not set
            if not shutil.which("tmux"):
                raise BinaryNotFoundError(
                    "tmux",
                    "sudo apt-get install tmux  # Debian/Ubuntu\n"
                    "sudo yum install tmux      # RHEL/CentOS\n"
                    "brew install tmux          # macOS\n"
                    "pacman -S tmux             # Arch Linux"
                )

    @property
    def client_capture_disabled(self) -> bool:
        """Check if client-side terminal capture is disabled."""
        return self.config.get("session_manager.disable_client_capture", True)

    @log_function(Logger.DEBUG)
    async def create_session(
        self,
        session_id: str,
        name: str,
        profile: Dict[str, Any],
        on_output: Optional[Callable[[str], None]] = None,
        on_clear: Optional[Callable[[], None]] = None,
        on_cursor: Optional[Callable[[int, int], None]] = None,
    ) -> Optional[BaseSession]:
        """Create a new session based on configured mode."""
        mode = self.config.session_manager_mode
        logger.info(f"Creating session with mode '{mode}': {session_id}")

        if mode == "tmux":
            socket_dir = self.config.get("session_manager.tmux.socket_dir", "~/.flashback-terminal/tmux")
            config_file = self.config.get("session_manager.tmux.config_file")
            init_commands = self.config.get("session_manager.tmux.init_commands", [])
            session = TmuxSession(session_id=session_id, name=name, profile=profile, socket_dir=socket_dir, on_output=on_output, on_clear=on_clear, on_cursor=on_cursor, init_commands=init_commands)
            session._config_file = config_file
        elif mode == "screen":
            socket_dir = self.config.get("session_manager.screen.socket_dir", "~/.flashback-terminal/screen")
            config_file = self.config.get("session_manager.screen.config_file")
            init_commands = self.config.get("session_manager.screen.init_commands", [])
            session = ScreenSession(session_id=session_id, name=name, profile=profile, socket_dir=socket_dir, on_output=on_output, init_commands=init_commands)
            session._config_file = config_file
        else:
            # Default to tmux if invalid mode
            logger.warning(f"Invalid session manager mode '{mode}', defaulting to tmux")
            socket_dir = self.config.get("session_manager.tmux.socket_dir", "~/.flashback-terminal/tmux")
            init_commands = self.config.get("session_manager.tmux.init_commands", [])
            session = TmuxSession(session_id=session_id, name=name, profile=profile, socket_dir=socket_dir, on_output=on_output, on_clear=on_clear, on_cursor=on_cursor, init_commands=init_commands)

        if await session.start():
            self._sessions[session_id] = session
            return session
        return None

    def get_session(self, session_id: str) -> Optional[BaseSession]:
        """Get session by ID."""
        return self._sessions.get(session_id)

    async def close_session(self, session_id: str) -> None:
        """Close a session."""
        if session_id in self._sessions:
            session = self._sessions[session_id]
            await session.stop()
            del self._sessions[session_id]

    async def list_sessions(self) -> List[SessionInfo]:
        """List all managed sessions."""
        sessions = []
        for session_id, session in self._sessions.items():
            sessions.append(SessionInfo(
                session_id=session_id,
                name=session.name,
                created_at=session._created_at,
                is_running=await session.is_running(),
            ))
        return sessions

    async def capture_session(
        self,
        session_id: str,
        full_scrollback: bool = False,
    ) -> Optional[SessionCapture]:
        """Capture session content."""
        session = self._sessions.get(session_id)
        if session:
            return await session.capture(full_scrollback)
        return None


def get_session_manager() -> SessionManager:
    """Get or create the global session manager instance."""
    global _singleton_session_manager
    if not _singleton_session_manager:
        logger.info("Creating global session manager instance")
        _singleton_session_manager = SessionManager()
    else:
        logger.info("Using existing global session manager instance")

    return _singleton_session_manager