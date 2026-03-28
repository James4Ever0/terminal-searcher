"""Backend terminal capture worker for flashback-terminal.

Captures terminal content from screen/tmux sessions and renders screenshots
using agg_python_bindings.
"""

import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from flashback_terminal.config import get_config
from flashback_terminal.database import Database
from flashback_terminal.logger import Logger, log_function, logger
from flashback_terminal.session_manager import SessionManager, SessionCapture


class CaptureWorker:
    """Worker that captures terminal content from screen/tmux sessions."""

    def __init__(self, db: Database):
        self.db = db
        self.config = get_config()
        self.session_manager = SessionManager()
        self._running = False
        self._last_capture: Dict[str, float] = {}  # session_id -> timestamp

        # Try to import agg_python_bindings
        try:
            import agg_python_bindings
            self._renderer = agg_python_bindings
            self._has_renderer = True
            logger.info("agg_python_bindings loaded successfully")
        except ImportError:
            self._renderer = None
            self._has_renderer = False
            logger.warning("agg_python_bindings not installed, screenshot rendering disabled")

    @property
    def enabled(self) -> bool:
        """Check if capture is enabled in config."""
        return self.config.get("session_manager.capture.enabled", True)

    @property
    def interval_seconds(self) -> int:
        """Get capture interval from config."""
        return self.config.get("session_manager.capture.interval_seconds", 10)

    @property
    def capture_full_scrollback(self) -> bool:
        """Check if full scrollback should be captured."""
        return self.config.get("session_manager.capture.capture_full_scrollback", True)

    def start(self) -> None:
        """Start the capture worker."""
        if not self.enabled:
            logger.info("Capture worker disabled in config")
            return

        if not self._has_renderer:
            logger.warning("Capture worker started but agg_python_bindings not available")
            # Still capture text content even without renderer

        self._running = True
        logger.info(f"Capture worker started (interval: {self.interval_seconds}s)")

    def stop(self) -> None:
        """Stop the capture worker."""
        self._running = False
        logger.info("Capture worker stopped")

    def capture_all_sessions(self) -> List[Dict[str, Any]]:
        """Capture all active sessions."""
        if not self._running:
            return []

        results = []
        sessions = self.session_manager.list_sessions()

        for session_info in sessions:
            if not session_info.is_running:
                continue

            # Check if enough time has passed since last capture
            session_id = session_info.session_id
            last_time = self._last_capture.get(session_id, 0)
            current_time = time.time()

            if current_time - last_time < self.interval_seconds:
                continue

            result = self.capture_session(session_id)
            if result:
                results.append(result)
                self._last_capture[session_id] = current_time

        return results

    @log_function(Logger.DEBUG)
    def capture_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Capture a single session."""
        try:
            # Capture from session manager
            capture = self.session_manager.capture_session(
                session_id, full_scrollback=self.capture_full_scrollback
            )

            if not capture:
                logger.debug(f"No capture data for session {session_id}")
                return None

            # Get database session record
            db_session = self.db.get_session_by_uuid(session_id)
            if not db_session:
                logger.warning(f"Session {session_id} not found in database")
                return None

            # Determine capture type
            config_mode = self.config.session_manager_mode
            capture_type = config_mode if config_mode in ("screen", "tmux") else "tmux"

            # Render screenshot if we have ANSI content and renderer is available
            screenshot_path = None
            if capture.ansi and self._has_renderer:
                screenshot_path = self._render_screenshot(
                    session_id, db_session.id, capture.ansi
                )

            # Save text content (OCR result)
            text_content = capture.text

            # Store in database
            capture_id = self.db.insert_terminal_capture(
                session_id=db_session.id,
                screenshot_path=screenshot_path,
                text_content=text_content,
                ansi_content=capture.ansi,
                capture_type=capture_type,
                metadata={
                    "session_name": capture.session_name,
                    "timestamp": capture.timestamp,
                },
            )

            logger.debug(f"Captured session {session_id}: capture_id={capture_id}")

            return {
                "capture_id": capture_id,
                "session_id": session_id,
                "screenshot_path": screenshot_path,
                "has_text": text_content is not None,
                "has_ansi": capture.ansi is not None,
            }

        except Exception as e:
            logger.error(f"Failed to capture session {session_id}: {e}")
            return None

    def _render_screenshot(
        self, session_uuid: str, session_db_id: int, ansi_content: str
    ) -> Optional[str]:
        """Render ANSI content to PNG screenshot.

        Uses agg_python_bindings.TerminalEmulator to render terminal output.
        """
        if not self._renderer:
            return None

        try:
            # Create screenshot directory
            screenshot_dir = Path(self.config.data_dir) / "screenshots" / str(session_db_id)
            screenshot_dir.mkdir(parents=True, exist_ok=True)

            # Generate filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = f"{session_uuid[:8]}_{timestamp}.png"
            filepath = screenshot_dir / filename

            # Determine terminal size (default 80x24 or from config)
            cols = self.config.get("terminal.cols", 80)
            rows = self.config.get("terminal.rows", 24)

            # Count actual lines in ANSI content to adjust rows
            line_count = len(ansi_content.splitlines())
            rows = max(rows, min(line_count, 100))  # Cap at 100 rows

            # Create terminal emulator and render
            emulator = self._renderer.TerminalEmulator(cols, rows)
            emulator.feed_str(ansi_content)
            emulator.screenshot(str(filepath))

            logger.debug(f"Rendered screenshot: {filepath}")
            return str(filepath)

        except Exception as e:
            logger.error(f"Failed to render screenshot: {e}")
            return None

    def run_once(self) -> List[Dict[str, Any]]:
        """Run capture once (for manual triggering)."""
        return self.capture_all_sessions()


class CaptureWorkerScheduler:
    """Scheduler for running capture worker at intervals."""

    def __init__(self, db: Database):
        self.worker = CaptureWorker(db)
        self._running = False

    def start(self) -> None:
        """Start the scheduler."""
        self.worker.start()
        self._running = True

        # Note: In production, this would use APScheduler or similar
        # For now, we just start the worker and let the caller manage scheduling
        logger.info("Capture scheduler started")

    def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        self.worker.stop()
        logger.info("Capture scheduler stopped")

    def run_captures(self) -> List[Dict[str, Any]]:
        """Run captures for all sessions."""
        if not self._running:
            return []
        return self.worker.capture_all_sessions()
