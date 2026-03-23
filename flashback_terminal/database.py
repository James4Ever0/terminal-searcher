"""Database models and operations for flashback-terminal."""

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


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


class Database:
    """SQLite database manager."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.init_db()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
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
                    metadata TEXT
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS terminal_output (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    sequence_num INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    content_type TEXT DEFAULT 'output'
                )
            """)

            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS terminal_output_fts USING fts5(
                    content,
                    content_rowid=rowid,
                    tokenize='porter'
                )
            """)

            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS terminal_output_ai
                AFTER INSERT ON terminal_output
                BEGIN
                    INSERT INTO terminal_output_fts(rowid, content) VALUES (new.id, new.content);
                END
            """)

            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS terminal_output_ad
                AFTER DELETE ON terminal_output
                BEGIN
                    INSERT INTO terminal_output_fts(terminal_output_fts, rowid, content)
                    VALUES ('delete', old.id, old.content);
                END
            """)

            conn.execute("""
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

            conn.execute("""
                CREATE TABLE IF NOT EXISTS embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    output_chunk_id INTEGER REFERENCES terminal_output(id) ON DELETE CASCADE,
                    vector_id TEXT NOT NULL,
                    model_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
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

            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_output_session ON terminal_output(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_output_sequence ON terminal_output(session_id, sequence_num)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_screenshots_session ON screenshots(session_id)")

            conn.commit()

    def create_session(
        self, uuid: str, name: str, profile_name: str, metadata: Optional[Dict] = None
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO sessions (uuid, name, profile_name, metadata) VALUES (?, ?, ?, ?)",
                (uuid, name, profile_name, json.dumps(metadata or {})),
            )
            conn.commit()
            return cursor.lastrowid

    def get_session(self, session_id: int) -> Optional[Session]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row:
                return self._row_to_session(row)
            return None

    def get_session_by_uuid(self, uuid: str) -> Optional[Session]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE uuid = ?", (uuid,)).fetchone()
            if row:
                return self._row_to_session(row)
            return None

    def update_session(self, session_id: int, **kwargs) -> None:
        allowed = {"name", "last_cwd", "status", "ended_at", "last_active_at", "metadata"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return

        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [session_id]

        with self._connect() as conn:
            conn.execute(f"UPDATE sessions SET {set_clause} WHERE id = ?", values)
            conn.commit()

    def list_sessions(
        self, status: Optional[str] = None, limit: int = 100, offset: int = 0
    ) -> List[Session]:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM sessions WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (status, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM sessions ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
            return [self._row_to_session(row) for row in rows]

    def insert_terminal_output(
        self, session_id: int, sequence_num: int, content: str, content_type: str = "output"
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO terminal_output (session_id, sequence_num, content, content_type) VALUES (?, ?, ?, ?)",
                (session_id, sequence_num, content, content_type),
            )
            conn.commit()
            return cursor.lastrowid

    def get_terminal_output(
        self, session_id: int, from_seq: Optional[int] = None, to_seq: Optional[int] = None
    ) -> List[TerminalOutput]:
        with self._connect() as conn:
            if from_seq is not None and to_seq is not None:
                rows = conn.execute(
                    """SELECT * FROM terminal_output
                       WHERE session_id = ? AND sequence_num BETWEEN ? AND ?
                       ORDER BY sequence_num""",
                    (session_id, from_seq, to_seq),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM terminal_output WHERE session_id = ? ORDER BY sequence_num",
                    (session_id,),
                ).fetchall()
            return [self._row_to_terminal_output(row) for row in rows]

    def get_terminal_output_by_id(self, output_id: int) -> Optional[TerminalOutput]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM terminal_output WHERE id = ?", (output_id,)).fetchone()
            if row:
                return self._row_to_terminal_output(row)
            return None

    def search_text(
        self, query: str, session_ids: Optional[List[int]] = None, limit: int = 50
    ) -> List[Dict]:
        with self._connect() as conn:
            if session_ids:
                placeholders = ",".join("?" * len(session_ids))
                rows = conn.execute(
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
                ).fetchall()
            else:
                rows = conn.execute(
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
                ).fetchall()
            return [dict(row) for row in rows]

    def insert_screenshot(
        self, session_id: int, file_path: str, file_size: int, width: int, height: int
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO screenshots (session_id, file_path, file_size, width, height) VALUES (?, ?, ?, ?, ?)",
                (session_id, file_path, file_size, width, height),
            )
            conn.commit()
            return cursor.lastrowid

    def get_screenshots(self, session_id: int, limit: int = 100) -> List[Screenshot]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM screenshots WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            return [self._row_to_screenshot(row) for row in rows]

    def get_sessions_older_than(self, days: int) -> List[int]:
        cutoff = datetime.now() - timedelta(days=days)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM sessions WHERE created_at < ? AND status != 'archived'",
                (cutoff,),
            ).fetchall()
            return [row[0] for row in rows]

    def delete_session(self, session_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()

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
