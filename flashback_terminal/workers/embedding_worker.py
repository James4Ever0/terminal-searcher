"""Background worker for generating text embeddings."""

import os
import time
from pathlib import Path
from typing import Optional

import requests

from flashback_terminal.config import get_config
from flashback_terminal.database import Database


class EmbeddingWorker:
    """Worker that generates embeddings for terminal output."""

    def __init__(self, db: Database):
        self.db = db
        self.config = get_config()
        self.api_config = self.config.get("workers.embedding.text", {})
        self.running = False

    def start(self):
        """Start the worker."""
        self.running = True
        print("[EmbeddingWorker] Started")

        while self.running:
            try:
                self._process_batch()
                time.sleep(self.config.get("workers.embedding.work_interval_seconds", 1))
            except Exception as e:
                print(f"[EmbeddingWorker] Error: {e}")
                time.sleep(5)

    def stop(self):
        """Stop the worker."""
        self.running = False

    def _process_batch(self):
        """Process a batch of unembedded outputs."""
        batch_size = self.config.get("workers.embedding.batch_size", 10)

        # Get unprocessed outputs
        with self.db._connect() as conn:
            rows = conn.execute(
                """SELECT o.* FROM terminal_output o
                   LEFT JOIN embeddings e ON o.id = e.output_chunk_id
                   WHERE e.id IS NULL
                   LIMIT ?""",
                (batch_size,),
            ).fetchall()

        for row in rows:
            self._process_output(row)

    def _process_output(self, row):
        """Generate embedding for a single output."""
        content = row["content"]
        output_id = row["id"]
        session_id = row["session_id"]

        try:
            embedding = self._get_embedding(content)
            self._save_embedding(session_id, output_id, embedding)
        except Exception as e:
            print(f"[EmbeddingWorker] Failed to process output {output_id}: {e}")

    def _get_embedding(self, text: str):
        """Get embedding from API."""
        base_url = self.api_config.get("base_url", "").rstrip("/")
        url = f"{base_url}/embeddings"

        headers = {"Content-Type": "application/json"}
        api_key = self.api_config.get("api_key", "")

        if api_key.startswith("${") and api_key.endswith("}"):
            api_key = os.environ.get(api_key[2:-1], "")

        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {"model": self.api_config.get("model"), "input": text}

        response = requests.post(url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()

        return response.json()["data"][0]["embedding"]

    def _save_embedding(self, session_id: int, output_id: int, embedding: list):
        """Save embedding to database and file."""
        import numpy as np

        # Save to file
        session = self.db.get_session(session_id)
        if session:
            emb_file = Path(self.config.embedding_dir) / f"{session.uuid}.npy"
            np.save(emb_file, np.array(embedding, dtype=np.float32))

        # Save reference to database
        with self.db._connect() as conn:
            conn.execute(
                """INSERT INTO embeddings (session_id, output_chunk_id, vector_id, model_name)
                   VALUES (?, ?, ?, ?)""",
                (session_id, output_id, str(output_id), self.api_config.get("model", "unknown")),
            )
            conn.commit()
