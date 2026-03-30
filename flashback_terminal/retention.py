"""Retention management for flashback-terminal - archive/delete old sessions."""

import hashlib
import json
import os
import shutil
import tarfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from flashback_terminal.config import get_config
from flashback_terminal.database import Database


class RetentionManager:
    """Manages session retention with archive or delete strategies."""

    def __init__(self, db: Database):
        self.db = db
        self.config = get_config()

    async def run_cleanup(self) -> None:
        """Run the retention cleanup process."""
        retention_days = self.config.retention_days
        strategy = self.config.get("workers.retention.strategy", "archive")

        print(
            f"[RetentionManager] Running cleanup (strategy: {strategy}, days: {retention_days})"
        )

        old_sessions = await self.db.get_sessions_older_than(retention_days)

        if not old_sessions:
            print("[RetentionManager] No old sessions to process")
            return

        print(f"[RetentionManager] Found {len(old_sessions)} sessions to process")

        if strategy == "delete":
            await self._delete_sessions(old_sessions)
        else:
            await self._archive_sessions(old_sessions)

        if strategy == "archive":
            await self._enforce_archive_constraints()

    async def _delete_sessions(self, session_ids: List[int]) -> None:
        """Permanently delete sessions."""
        for session_id in session_ids:
            await self._delete_session_data(session_id)
            await self.db.delete_session(session_id)
            print(f"[RetentionManager] Deleted session {session_id}")

    async def _delete_session_data(self, session_id: int) -> None:
        """Delete all data associated with a session."""
        config = self.config
        session = await self.db.get_session(session_id)
        if not session:
            return

        log_file = Path(config.log_dir) / f"{session.uuid}.log"
        if log_file.exists():
            log_file.unlink()

        screenshot_dir = Path(config.screenshot_dir) / session.uuid
        if screenshot_dir.exists():
            shutil.rmtree(screenshot_dir)

        embedding_file = Path(config.embedding_dir) / f"{session.uuid}.npy"
        if embedding_file.exists():
            embedding_file.unlink()

    async def _archive_sessions(self, session_ids: List[int]) -> None:
        """Archive sessions to compressed storage."""
        if not session_ids:
            return

        config = self.config
        archive_dir = Path(config.archive_dir)
        organization = config.get("workers.retention.archive.organization", "monthly")
        compression = config.get("workers.retention.archive.compression", "gzip")

        inprogress_dir = archive_dir / "archive.inprogress"
        inprogress_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = f"archive-{timestamp}"
        work_dir = inprogress_dir / archive_name
        work_dir.mkdir(parents=True, exist_ok=True)

        try:
            sessions_data = []
            output_count = 0
            screenshot_count = 0
            date_from: Optional[datetime] = None
            date_to: Optional[datetime] = None

            for session_id in session_ids:
                session = await self.db.get_session(session_id)
                if not session:
                    continue

                session_data = await self._archive_session(session, work_dir)
                if session_data:
                    sessions_data.append(session_data)
                    output_count += session_data.get("output_count", 0)
                    screenshot_count += session_data.get("screenshot_count", 0)

                    if date_from is None or session.created_at < date_from:
                        date_from = session.created_at
                    if date_to is None or session.created_at > date_to:
                        date_to = session.created_at

            if not sessions_data:
                shutil.rmtree(work_dir)
                return

            manifest = {
                "created_at": datetime.now().isoformat(),
                "date_from": date_from.isoformat() if date_from else None,
                "date_to": date_to.isoformat() if date_to else None,
                "session_count": len(sessions_data),
                "output_count": output_count,
                "screenshot_count": screenshot_count,
                "sessions": sessions_data,
                "checksums": self._calculate_checksums(work_dir),
            }

            manifest_path = work_dir / "manifest.json"
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)

            ext = {"gzip": ".tar.gz", "bz2": ".tar.bz2", "xz": ".tar.xz", None: ".tar"}.get(
                compression, ".tar.gz"
            )
            archive_filename = f"{archive_name}{ext}"

            if organization == "monthly":
                month_dir = archive_dir / date_from.strftime("%Y-%m")  # type: ignore
            elif organization == "yearly":
                month_dir = archive_dir / date_from.strftime("%Y")  # type: ignore
            else:
                month_dir = archive_dir

            month_dir.mkdir(parents=True, exist_ok=True)
            final_path = month_dir / archive_filename

            mode = {"gzip": "w:gz", "bz2": "w:bz2", "xz": "w:xz", None: "w"}.get(
                compression, "w:gz"
            )
            with tarfile.open(final_path, mode) as tar:
                tar.add(work_dir, arcname=archive_name)

            archive_size = final_path.stat().st_size

            async with self.db._connect() as conn:
                await conn.execute(
                    """
                    INSERT INTO archive_manifest
                    (archive_path, session_count, output_count, screenshot_count,
                     date_from, date_to, size_bytes, compression)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        str(final_path),
                        len(sessions_data),
                        output_count,
                        screenshot_count,
                        date_from,
                        date_to,
                        archive_size,
                        compression,
                    ),
                )
                conn.commit()

            for session_id in session_ids:
                self._delete_session_data(session_id)
                await self.db.update_session(session_id, status="archived")

            shutil.rmtree(work_dir)

            print(
                f"[RetentionManager] Archived {len(sessions_data)} sessions to {final_path}"
            )

        except Exception as e:
            print(f"[RetentionManager] Archive failed: {e}")
            raise

    async def _archive_session(self, session, work_dir: Path) -> Optional[Dict]:
        """Archive a single session's data."""
        config = self.config
        session_dir = work_dir / "sessions" / session.uuid
        session_dir.mkdir(parents=True, exist_ok=True)

        session_data = {
            "id": session.id,
            "uuid": session.uuid,
            "name": session.name,
            "profile_name": session.profile_name,
            "created_at": session.created_at.isoformat(),
            "last_cwd": session.last_cwd,
            "metadata": session.metadata,
        }

        with open(session_dir / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(session_data, f, indent=2)

        outputs = await self.db.get_terminal_output(session.id)
        output_count = len(outputs)

        with open(session_dir / "output.jsonl", "w", encoding="utf-8") as f:
            for output in outputs:
                f.write(
                    json.dumps(
                        {
                            "sequence_num": output.sequence_num,
                            "timestamp": output.timestamp.isoformat(),
                            "content": output.content,
                            "content_type": output.content_type,
                        }
                    )
                    + "\n"
                )

        screenshot_dir = Path(config.screenshot_dir) / session.uuid
        screenshot_count = 0

        if screenshot_dir.exists():
            dest_screenshot_dir = session_dir / "screenshots"
            dest_screenshot_dir.mkdir(parents=True, exist_ok=True)

            for img_file in screenshot_dir.iterdir():
                if img_file.suffix in [".png", ".jpg", ".jpeg"]:
                    shutil.copy2(img_file, dest_screenshot_dir / img_file.name)
                    screenshot_count += 1

        return {
            "uuid": session.uuid,
            "output_count": output_count,
            "screenshot_count": screenshot_count,
        }

    def _calculate_checksums(self, work_dir: Path) -> Dict[str, str]:
        """Calculate SHA256 checksums for all files in work_dir."""
        checksums = {}
        for filepath in work_dir.rglob("*"):
            if filepath.is_file():
                rel_path = filepath.relative_to(work_dir)
                sha256 = hashlib.sha256()
                with open(filepath, "rb") as f:
                    for chunk in iter(lambda: f.read(8192), b""):
                        sha256.update(chunk)
                checksums[str(rel_path)] = sha256.hexdigest()
        return checksums

    async def _enforce_archive_constraints(self) -> None:
        """Enforce archive size and age constraints."""
        config = self.config
        max_size = config.get("workers.retention.archive.total_size_limit", 0)
        max_age = config.get("workers.retention.archive.max_age_days", 0)

        if max_size == 0 and max_age == 0:
            return

        archive_dir = Path(config.archive_dir)

        async with self.db._connect() as conn:
            rows = await (await conn.execute(
                "SELECT * FROM archive_manifest ORDER BY created_at ASC"
            )).fetchall()

        total_size = sum(row["size_bytes"] for row in rows)
        now = datetime.now()

        for row in rows:
            archive_path = Path(row["archive_path"])
            should_delete = False

            if max_age > 0:
                archive_age_days = (now - datetime.fromisoformat(row["created_at"])).days
                if archive_age_days > max_age:
                    should_delete = True
                    print(f"[RetentionManager] Archive exceeds max age: {archive_path}")

            if max_size > 0 and total_size > max_size:
                should_delete = True
                print(f"[RetentionManager] Archive exceeds total size limit: {archive_path}")

            if should_delete:
                if archive_path.exists():
                    archive_path.unlink()
                total_size -= row["size_bytes"]

                async with self.db._connect() as conn:
                    await conn.execute(
                        "DELETE FROM archive_manifest WHERE id = ?", (row["id"],)
                    )
                    await conn.commit()

    async def restore_session(self, archive_path: Path, uuid: str) -> bool:
        """Restore a session from archive."""
        if not archive_path.exists():
            return False

        config = self.config
        work_dir = Path(config.archive_dir) / "archive.inprogress" / "restore"
        work_dir.mkdir(parents=True, exist_ok=True)

        try:
            with tarfile.open(archive_path, "r") as tar:
                tar.extractall(work_dir)

            session_dir = None
            for path in work_dir.rglob("sessions"):
                if path.is_dir():
                    session_dir = path / uuid
                    break

            if not session_dir or not session_dir.exists():
                shutil.rmtree(work_dir)
                return False

            with open(session_dir / "metadata.json", encoding="utf-8") as f:
                metadata = json.load(f)

            session_id = await self.db.create_session(
                uuid=metadata["uuid"],
                name=metadata["name"],
                profile_name=metadata["profile_name"],
                metadata=metadata.get("metadata", {}),
            )

            await self.db.update_session(
                session_id, last_cwd=metadata.get("last_cwd"), status="inactive"
            )

            output_file = session_dir / "output.jsonl"
            if output_file.exists():
                with open(output_file, encoding="utf-8") as f:
                    for line in f:
                        record = json.loads(line)
                        await self.db.insert_terminal_output(
                            session_id=session_id,
                            sequence_num=record["sequence_num"],
                            content=record["content"],
                            content_type=record.get("content_type", "output"),
                        )

            screenshot_dir = session_dir / "screenshots"
            if screenshot_dir.exists():
                dest_dir = Path(config.screenshot_dir) / uuid
                dest_dir.mkdir(parents=True, exist_ok=True)

                for img_file in screenshot_dir.iterdir():
                    shutil.copy2(img_file, dest_dir / img_file.name)

            shutil.rmtree(work_dir)

            print(f"[RetentionManager] Restored session {uuid}")
            return True

        except Exception as e:
            print(f"[RetentionManager] Restore failed: {e}")
            if work_dir.exists():
                shutil.rmtree(work_dir)
            return False
