"""Database models and operations for flashback-terminal."""

import json
import aiosqlite
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from flashback_terminal.logger import Logger, log_function, logger


@dataclass
class Session:
    id: int
    uuid: str
    name: str
    profile_name: str
    created_at: datetime
    updated_at: datetime
    last_active_at: Optional[datetime]
    ended_at: Optional[datetime]
    last_cwd: Optional[str]
    status: str
    metadata: Dict[str, Any]
    session_type: str = "tmux"  # 'screen' or 'tmux'
    socket_path: Optional[str] = None
    pane_id: Optional[str] = None
    window_id: Optional[str] = None


@dataclass
class TerminalOutput:
    id: int
    session_id: int
    timestamp: datetime
    sequence_num: int
    content: str
    content_type: str


@dataclass
class Screenshot:
    id: int
    session_id: int
    timestamp: datetime
    file_path: str
    file_size: int
    width: int
    height: int


@dataclass
class TerminalCapture:
    """Backend terminal capture for screen/tmux sessions."""
    id: int
    session_id: int
    timestamp: datetime
    screenshot_path: Optional[str]
    text_content: Optional[str]
    ansi_content: Optional[str]
    capture_type: str  # 'screen', 'tmux', 'frontend'


class Database:
    """SQLite database manager."""

    def __init__(self, db_path: Path):
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        try:
            yield conn
        finally:
            await conn.close()

    async def init_db(self) -> None:
        async with self._connect() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uuid TEXT UNIQUE NOT NULL,
                    name TEXT,
                    profile_name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_active_at TIMESTAMP,
                    ended_at TIMESTAMP,
                    last_cwd TEXT,
                    status TEXT DEFAULT 'active',
                    metadata TEXT,
                    session_type TEXT DEFAULT 'tmux',  -- 'screen' or 'tmux'
                    socket_path TEXT,                   -- path to socket file
                    pane_id TEXT,                       -- for tmux: pane identifier
                    window_id TEXT                      -- for tmux: window identifier
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS terminal_output (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    sequence_num INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    content_type TEXT DEFAULT 'output'
                )
            """)

            await conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS terminal_output_fts USING fts5(
                    content,
                    content_rowid=rowid,
                    tokenize='porter'
                )
            """)

            await conn.execute("""
                CREATE TRIGGER IF NOT EXISTS terminal_output_ai
                AFTER INSERT ON terminal_output
                BEGIN
                    INSERT INTO terminal_output_fts(rowid, content) VALUES (new.id, new.content);
                END
            """)

            await conn.execute("""
                CREATE TRIGGER IF NOT EXISTS terminal_output_ad
                AFTER DELETE ON terminal_output
                BEGIN
                    INSERT INTO terminal_output_fts(terminal_output_fts, rowid, content)
                    VALUES ('delete', old.id, old.content);
                END
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS screenshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    file_path TEXT NOT NULL,
                    file_size INTEGER,
                    width INTEGER,
                    height INTEGER
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS terminal_captures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    screenshot_path TEXT,              -- Path to PNG screenshot (backend rendered)
                    text_content TEXT,                 -- Plain text content (OCR result)
                    ansi_content TEXT,                 -- ANSI escape sequences
                    capture_type TEXT DEFAULT 'tmux',  -- 'screen', 'tmux', or 'frontend'
                    metadata TEXT                      -- JSON with additional capture info
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    output_chunk_id INTEGER REFERENCES terminal_output(id) ON DELETE CASCADE,
                    vector_id TEXT NOT NULL,
                    model_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS archive_manifest (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    archive_path TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    session_count INTEGER,
                    output_count INTEGER,
                    screenshot_count INTEGER,
                    date_from TIMESTAMP,
                    date_to TIMESTAMP,
                    size_bytes INTEGER,
                    compression TEXT
                )
            """)

            await conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_output_session ON terminal_output(session_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_output_sequence ON terminal_output(session_id, sequence_num)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_screenshots_session ON screenshots(session_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_captures_session ON terminal_captures(session_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_captures_timestamp ON terminal_captures(timestamp)")

            await conn.commit()

    @log_function(Logger.DEBUG)
    async def create_session(
        self,
        uuid: str,
        name: str,
        profile_name: str,
        metadata: Optional[Dict] = None,
        session_type: str = "tmux",
        socket_path: Optional[str] = None,
        pane_id: Optional[str] = None,
        window_id: Optional[str] = None,
    ) -> int:
        logger.debug(f"Creating session: uuid={uuid}, name={name}, profile={profile_name}, type={session_type}")
        async with self._connect() as conn:
            cursor = await conn.execute(
                """INSERT INTO sessions
                    (uuid, name, profile_name, metadata, session_type, socket_path, pane_id, window_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (uuid, name, profile_name, json.dumps(metadata or {}),
                 session_type, socket_path, pane_id, window_id),
            )
            await conn.commit()
            session_id = cursor.lastrowid
            logger.info(f"Session created: id={session_id}, uuid={uuid}, type={session_type}")
            return session_id

    async def get_session(self, session_id: int) -> Optional[Session]:
        async with self._connect() as conn:
            row = await (await conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))).fetchone()
            if row:
                return self._row_to_session(row)
            return None

    async def get_session_by_uuid(self, uuid: str) -> Optional[Session]:
        async with self._connect() as conn:
            row = await (await conn.execute("SELECT * FROM sessions WHERE uuid = ?", (uuid,))).fetchone()
            if row:
                return self._row_to_session(row)
            return None

    async def update_session(self, session_id: int, **kwargs) -> None:
        allowed = {"name", "last_cwd", "status", "ended_at", "last_active_at", "metadata",
                   "session_type", "socket_path", "pane_id", "window_id"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return

        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [session_id]

        async with self._connect() as conn:
            await conn.execute(f"UPDATE sessions SET {set_clause} WHERE id = ?", values)
            await conn.commit()

    async def list_sessions(
        self, status: Optional[str] = None, limit: int = 100, offset: int = 0
    ) -> List[Session]:
        async with self._connect() as conn:
            if status:
                rows = await (await conn.execute(
                    "SELECT * FROM sessions WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (status, limit, offset),
                )).fetchall()
            else:
                rows = await (await conn.execute(
                    "SELECT * FROM sessions ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )).fetchall()
            return [self._row_to_session(row) for row in rows]

    async def insert_terminal_output(
        self, session_id: int, sequence_num: int, content: str, content_type: str = "output"
    ) -> int:
        async with self._connect() as conn:
            cursor = await conn.execute(
                "INSERT INTO terminal_output (session_id, sequence_num, content, content_type) VALUES (?, ?, ?, ?)",
                (session_id, sequence_num, content, content_type),
            )
            await conn.commit()
            return cursor.lastrowid

    async def get_terminal_output(
        self, session_id: int, from_seq: Optional[int] = None, to_seq: Optional[int] = None
    ) -> List[TerminalOutput]:
        async with self._connect() as conn:
            if from_seq is not None and to_seq is not None:
                rows = await (await conn.execute(
                    """SELECT * FROM terminal_output
                       WHERE session_id = ? AND sequence_num BETWEEN ? AND ?
                       ORDER BY sequence_num""",
                    (session_id, from_seq, to_seq),
                )).fetchall()
            else:
                rows = await (await conn.execute(
                    "SELECT * FROM terminal_output WHERE session_id = ? ORDER BY sequence_num",
                    (session_id,),
                )).fetchall()
            return [self._row_to_terminal_output(row) for row in rows]

    async def get_terminal_output_by_id(self, output_id: int) -> Optional[TerminalOutput]:
        async with self._connect() as conn:
            row = await (await conn.execute("SELECT * FROM terminal_output WHERE id = ?", (output_id,))).fetchone()
            if row:
                return self._row_to_terminal_output(row)
            return None

    async def search_text(
        self, query: str, session_ids: Optional[List[int]] = None, limit: int = 50
    ) -> List[Dict]:
        async with self._connect() as conn:
            if session_ids:
                placeholders = ",".join("?" * len(session_ids))
                rows = await (await conn.execute(
                    f"""
                    SELECT o.*, s.uuid as session_uuid, s.name as session_name
                    FROM terminal_output_fts fts
                    JOIN terminal_output o ON fts.rowid = o.id
                    JOIN sessions s ON o.session_id = s.id
                    WHERE terminal_output_fts MATCH ? AND s.id IN ({placeholders})
                    ORDER BY rank
                    LIMIT ?
                """,
                    (query, *session_ids, limit),
                )).fetchall()
            else:
                rows = await (await conn.execute(
                    """
                    SELECT o.*, s.uuid as session_uuid, s.name as session_name
                    FROM terminal_output_fts fts
                    JOIN terminal_output o ON fts.rowid = o.id
                    JOIN sessions s ON o.session_id = s.id
                    WHERE terminal_output_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """,
                    (query, limit),
                )).fetchall()
            return [dict(row) for row in rows]

    async def insert_screenshot(
        self, session_id: int, file_path: str, file_size: int, width: int, height: int
    ) -> int:
        async with self._connect() as conn:
            cursor = await conn.execute(
                "INSERT INTO screenshots (session_id, file_path, file_size, width, height) VALUES (?, ?, ?, ?, ?)",
                (session_id, file_path, file_size, width, height),
            )
            await conn.commit()
            return cursor.lastrowid

    async def get_screenshots(self, session_id: int, limit: int = 100) -> List[Screenshot]:
        async with self._connect() as conn:
            rows = await (await conn.execute(
                "SELECT * FROM screenshots WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
                (session_id, limit),
            )).fetchall()
            return [self._row_to_screenshot(row) for row in rows]

    async def get_sessions_older_than(self, days: int) -> List[int]:
        cutoff = datetime.now() - timedelta(days=days)
        async with self._connect() as conn:
            rows = await (await conn.execute(
                "SELECT id FROM sessions WHERE created_at < ? AND status != 'archived'",
                (cutoff,),
            )).fetchall()
            return [row[0] for row in rows]

    async def delete_session(self, session_id: int) -> None:
        async with self._connect() as conn:
            await conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            await conn.commit()

    def _row_to_session(self, row) -> Session:
        return Session(
            id=row["id"],
            uuid=row["uuid"],
            name=row["name"],
            profile_name=row["profile_name"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            last_active_at=datetime.fromisoformat(row["last_active_at"]) if row["last_active_at"] else None,
            ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
            last_cwd=row["last_cwd"],
            status=row["status"],
            metadata=json.loads(row["metadata"] or "{}"),
            session_type=row["session_type"] or "tmux",
            socket_path=row["socket_path"],
            pane_id=row["pane_id"],
            window_id=row["window_id"],
        )

    def _row_to_terminal_output(self, row) -> TerminalOutput:
        return TerminalOutput(
            id=row["id"],
            session_id=row["session_id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            sequence_num=row["sequence_num"],
            content=row["content"],
            content_type=row["content_type"],
        )

    def _row_to_screenshot(self, row) -> Screenshot:
        return Screenshot(
            id=row["id"],
            session_id=row["session_id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            file_path=row["file_path"],
            file_size=row["file_size"],
            width=row["width"],
            height=row["height"],
        )

    async def insert_terminal_capture(
        self,
        session_id: int,
        screenshot_path: Optional[str] = None,
        text_content: Optional[str] = None,
        ansi_content: Optional[str] = None,
        capture_type: str = "tmux",
        metadata: Optional[Dict] = None,
    ) -> int:
        """Insert a terminal capture record (backend screenshot from screen/tmux)."""
        async with self._connect() as conn:
            cursor = await conn.execute(
                """INSERT INTO terminal_captures
                    (session_id, screenshot_path, text_content, ansi_content, capture_type, metadata)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                (session_id, screenshot_path, text_content, ansi_content,
                 capture_type, json.dumps(metadata or {})),
            )
            await conn.commit()
            return cursor.lastrowid

    async def get_terminal_captures(
        self, session_id: int, limit: int = 100, offset: int = 0
    ) -> List[TerminalCapture]:
        """Get terminal captures for a session."""
        async with self._connect() as conn:
            rows = await (await conn.execute(
                """SELECT * FROM terminal_captures
                   WHERE session_id = ?
                   ORDER BY timestamp DESC
                   LIMIT ? OFFSET ?""",
                (session_id, limit, offset),
            )).fetchall()
            return [self._row_to_capture(row) for row in rows]

    async def get_terminal_captures_timeline(
        self,
        before_time: Optional[float] = None,
        around_time: Optional[float] = None,
        limit: int = 50,
    ) -> List[Dict]:
        """Get terminal captures for timeline view."""
        async with self._connect() as conn:
            if around_time:
                # Get captures around a specific time
                rows = await (await conn.execute(
                    """SELECT tc.*, s.uuid as session_uuid, s.name as session_name
                       FROM terminal_captures tc
                       JOIN sessions s ON tc.session_id = s.id
                       WHERE abs(strftime('%s', tc.timestamp) - ?) < 300
                       ORDER BY tc.timestamp DESC
                       LIMIT ?""",
                    (around_time, limit),
                )).fetchall()
            elif before_time:
                # Get captures before a specific time
                rows = await (await conn.execute(
                    """SELECT tc.*, s.uuid as session_uuid, s.name as session_name
                       FROM terminal_captures tc
                       JOIN sessions s ON tc.session_id = s.id
                       WHERE strftime('%s', tc.timestamp) < ?
                       ORDER BY tc.timestamp DESC
                       LIMIT ?""",
                    (before_time, limit),
                )).fetchall()
            else:
                # Get most recent captures
                rows = await (await conn.execute(
                    """SELECT tc.*, s.uuid as session_uuid, s.name as session_name
                       FROM terminal_captures tc
                       JOIN sessions s ON tc.session_id = s.id
                       ORDER BY tc.timestamp DESC
                       LIMIT ?""",
                    (limit,),
                )).fetchall()
            return [dict(row) for row in rows]

    async def get_terminal_capture_by_id(self, capture_id: int) -> Optional[Dict]:
        """Get a single terminal capture with session info."""
        async with self._connect() as conn:
            row = await (await conn.execute(
                """SELECT tc.*, s.uuid as session_uuid, s.name as session_name
                   FROM terminal_captures tc
                   JOIN sessions s ON tc.session_id = s.id
                   WHERE tc.id = ?""",
                (capture_id,),
            )).fetchone()
            if row:
                return dict(row)
            return None

    async def get_terminal_capture_neighbors(
        self, capture_id: int, before: int = 5, after: int = 5
    ) -> List[Dict]:
        """Get neighboring captures for timeline context."""
        async with self._connect() as conn:
            # First get the timestamp of the reference capture
            ref = await (await conn.execute(
                "SELECT timestamp FROM terminal_captures WHERE id = ?",
                (capture_id,),
            )).fetchone()
            if not ref:
                return []

            ref_time = ref["timestamp"]

            # Get captures before
            before_rows = await (await conn.execute(
                """SELECT tc.*, s.uuid as session_uuid, s.name as session_name
                   FROM terminal_captures tc
                   JOIN sessions s ON tc.session_id = s.id
                   WHERE tc.timestamp < ?
                   ORDER BY tc.timestamp DESC
                   LIMIT ?""",
                (ref_time, before),
            )).fetchall()

            # Get captures after
            after_rows = await (await conn.execute(
                """SELECT tc.*, s.uuid as session_uuid, s.name as session_name
                   FROM terminal_captures tc
                   JOIN sessions s ON tc.session_id = s.id
                   WHERE tc.timestamp > ?
                   ORDER BY tc.timestamp ASC
                   LIMIT ?""",
                (ref_time, after),
            )).fetchall()

            # Combine and mark center
            results = []
            for row in reversed(before_rows):
                d = dict(row)
                d["is_center"] = False
                results.append(d)

            center = await (await conn.execute(
                """SELECT tc.*, s.uuid as session_uuid, s.name as session_name
                   FROM terminal_captures tc
                   JOIN sessions s ON tc.session_id = s.id
                   WHERE tc.id = ?""",
                (capture_id,),
            )).fetchone()
            if center:
                c = dict(center)
                c["is_center"] = True
                results.append(c)

            for row in after_rows:
                d = dict(row)
                d["is_center"] = False
                results.append(d)

            return results

    def _row_to_capture(self, row) -> TerminalCapture:
        return TerminalCapture(
            id=row["id"],
            session_id=row["session_id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            screenshot_path=row["screenshot_path"],
            text_content=row["text_content"],
            ansi_content=row["ansi_content"],
            capture_type=row["capture_type"],
        )
