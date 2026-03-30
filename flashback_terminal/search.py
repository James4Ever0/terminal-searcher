"""Search functionality for flashback-terminal."""

import asyncio
import math
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from flashback_terminal.logger import logger
from flashback_terminal.bm25_index import BM25SQLiteIndexAsync


try:
    import jieba

    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False

from flashback_terminal.config import get_config
from flashback_terminal.database import Database


class BM25Search:
    """BM25 text search over terminal output using async SQLite index."""

    def __init__(self, db: Database):
        self.db = db
        self.config = get_config()
        self.k1 = self.config.get("search.bm25.k1", 1.5)
        self.b = self.config.get("search.bm25.b", 0.75)
        self.rebuild_interval = self.config.get("search.bm25.rebuild_interval_seconds", 10)
        
        # Initialize BM25 index
        index_path = Path(self.config.search_index_dir) / "bm25_index.db"
        self.bm25_index = BM25SQLiteIndexAsync(
            db_path=str(index_path),
            tokenizer=self._tokenize,
            k1=self.k1,
            b=self.b
        )
        
        # Background task for index rebuilding
        self._rebuild_task = None
        self._initialized = False

    def _tokenize(self, text: str) -> List[str]:
        if JIEBA_AVAILABLE:
            return list(jieba.lcut_for_search(text.lower()))
        return re.findall(r"\b\w+\b", text.lower())
    
    async def initialize(self) -> None:
        """Initialize the BM25 index and start background rebuilding."""
        if not self._initialized:
            await self.bm25_index.initialize()
            await self._build_index()
            self._start_background_rebuild()
            self._initialized = True
    
    async def _build_index(self) -> None:
        """Build the BM25 index from terminal captures."""
        logger.debug("[BM25Search] Building index from terminal captures")
        
        async with self.db._connect() as conn:
            rows = await (await conn.execute(
                "SELECT id, session_id, text_content FROM terminal_captures WHERE text_content IS NOT NULL"
            )).fetchall()
        
        logger.debug(f"[BM25Search] Processing {len(rows)} terminal output records")
        
        # Clear existing index and rebuild
        await self.bm25_index.clear_all()
        
        # Add documents to index
        for row in rows:
            doc_id = row["id"]
            content = row["text_content"]
            try:
                await self.bm25_index.add_document(doc_id, content)
            except Exception as e:
                logger.warning(f"[BM25Search] Failed to add document {doc_id}: {e}")
        
        logger.debug(f"[BM25Search] Index rebuilt with {self.bm25_index.num_docs} documents")

    def search(
        self, query: str, session_ids: Optional[List[int]] = None, top_k: int = 50
    ) -> List[Tuple[int, float]]:
        """Search using the BM25 index."""
        if not self._initialized:
            return []
        
        # Get raw results from BM25 index
        raw_results = self.bm25_index.query(query, top_k * 2)  # Get more to filter
        
        # Convert doc_id strings to integers and filter by session if needed
        results = []
        for doc_id_str, score in raw_results:
            try:
                doc_id = int(doc_id_str)
                # If session filtering is needed, we'd need to look up the session_id
                # For now, we'll include all results and let the SearchEngine handle filtering
                results.append((doc_id, score))
            except ValueError:
                continue  # Skip invalid doc_ids
        
        return results[:top_k]
    
    def _start_background_rebuild(self) -> None:
        """Start the background task to rebuild the index periodically."""
        if self._rebuild_task is None or self._rebuild_task.done():
            logger.debug("[BM25Search] Starting background rebuild task")
            self._rebuild_task = asyncio.create_task(self._background_rebuild_loop())
        else:
            logger.debug("[BM25Search] Background rebuild task already running")
    
    async def _background_rebuild_loop(self) -> None:
        """Background loop that rebuilds the index periodically."""
        while True:
            try:
                logger.debug("[BM25Search] Waiting for rebuild interval...")
                await asyncio.sleep(self.rebuild_interval)
                logger.debug("[BM25Search] Building index...")
                await self._build_index()
            except asyncio.CancelledError:
                logger.debug("[BM25Search] Background rebuild task cancelled")
                break
            except Exception as e:
                logger.error(f"[BM25Search] Background rebuild failed: {e}")
    
    async def close(self) -> None:
        """Close the BM25 search and cleanup resources."""
        if self._rebuild_task and not self._rebuild_task.done():
            self._rebuild_task.cancel()
            try:
                await self._rebuild_task
            except asyncio.CancelledError:
                pass
        
        await self.bm25_index.close()


class EmbeddingSearch:
    """Semantic search using text embeddings via API."""

    def __init__(self, db: Database):
        self.db = db
        self.config = get_config()
        self.api_config = self.config.get("workers.embedding.text", {})
        self.dimension = self.api_config.get("dimension")

        if not self.dimension:
            raise RuntimeError(
                "Embedding dimension not configured. "
                "Run 'flashback-terminal config test-embedding --write'"
            )

    def _get_embedding(self, text: str) -> List[float]:
        import os

        import requests

        base_url = self.api_config.get("base_url", "").rstrip("/")
        url = f"{base_url}/embeddings"

        headers = {"Content-Type": "application/json"}
        api_key = self.api_config.get("api_key", "")

        if api_key.startswith("${") and api_key.endswith("}"):
            env_var = api_key[2:-1]
            api_key = os.environ.get(env_var, "")

        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {"model": self.api_config.get("model"), "input": text}

        response = requests.post(url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()

        data = response.json()
        return data["data"][0]["embedding"]

    async def search(
        self, query: str, session_ids: Optional[List[int]] = None, top_k: int = 50
    ) -> List[Tuple[int, float]]:
        import numpy as np

        query_vec = np.array(self._get_embedding(query), dtype=np.float32)
        query_norm = np.linalg.norm(query_vec)

        embedding_dir = Path(self.config.embedding_dir)
        scores = []

        for emb_file in embedding_dir.glob("*.npy"):
            try:
                emb = np.load(emb_file)
                if len(emb) != self.dimension:
                    continue

                similarity = np.dot(query_vec, emb) / (query_norm * np.linalg.norm(emb))

                session_uuid = emb_file.stem
                session = await self.db.get_session_by_uuid(session_uuid)
                if session:
                    if session_ids and session.id not in session_ids:
                        continue
                    scores.append((session.id, float(similarity)))
            except Exception:
                continue

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]


def reciprocal_rank_fusion(
    *results_lists: List[List[Tuple[int, float]]], k: int = 60, top_k: int = 50
) -> List[Tuple[int, float]]:
    """Merge multiple result lists using Reciprocal Rank Fusion."""
    fused_scores: Dict[int, float] = defaultdict(float)

    for results in results_lists:
        for rank, (doc_id, _) in enumerate(results):
            fused_scores[doc_id] += 1 / (k + rank + 1)

    sorted_results = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)
    return sorted_results[:top_k]


class SearchEngine:
    """Unified search combining BM25 and embedding search."""

    def __init__(self, db: Database):
        self.db = db
        self.config = get_config()
        self.bm25 = BM25Search(db) if self.config.is_search_enabled("bm25") else None
        self.embedding = None

        if self.config.is_search_enabled("embedding"):
            try:
                self.embedding = EmbeddingSearch(db)
            except Exception as e:
                print(f"[SearchEngine] Embedding search not available: {e}")
    
    async def initialize(self) -> None:
        """Initialize all search components."""
        if self.bm25:
            await self.bm25.initialize()
        # Embedding search doesn't need initialization
        logger.debug("[SearchEngine] Search components initialized")

    async def search(
        self,
        query: str,
        mode: str = "text",
        scope: str = "all",
        session_ids: Optional[List[int]] = None,
        limit: int = 50,
        order_by: str = "relevance",
        time_range: Optional[str] = None,
        filter_inactive: bool = False,
    ) -> List[Dict]:
        if mode == "text":
            if not self.bm25:
                return []
            # Index is built automatically in background, just search
            results = self.bm25.search(query, session_ids, limit)

        elif mode == "semantic":
            if not self.embedding:
                return []
            results = await self.embedding.search(query, session_ids, limit)

        elif mode == "hybrid":
            bm25_results = []
            embedding_results = []

            if self.bm25:
                bm25_results = self.bm25.search(query, session_ids, limit * 2)
            if self.embedding:
                embedding_results = await self.embedding.search(query, session_ids, limit * 2)

            rrf_k = self.config.get("modules.semantic_search.rrf_k", 60)
            results = reciprocal_rank_fusion(bm25_results, embedding_results, k=rrf_k, top_k=limit)

        else:
            raise ValueError(f"Unknown search mode: {mode}")

        enriched = []
        for doc_id, score in results:
            capture = await self.db.get_terminal_capture_by_id(doc_id)
            if capture:
                session = await self.db.get_session(capture["session_id"])
                
                # Apply time range filter if specified
                if time_range:
                    timestamp = datetime.fromisoformat(capture["timestamp"])
                    now = datetime.now()
                    if time_range == "1h" and (now - timestamp).total_seconds() > 3600:
                        continue
                    elif time_range == "24h" and (now - timestamp).total_seconds() > 86400:
                        continue
                    elif time_range == "7d" and (now - timestamp).days > 7:
                        continue
                    elif time_range == "30d" and (now - timestamp).days > 30:
                        continue
                
                # Apply inactive session filter if specified
                if filter_inactive and session and session.status != "active":
                    continue
                
                enriched.append(
                    {
                        "capture_id": doc_id,
                        "session_id": capture["session_id"],
                        "session_uuid": session.uuid if session else None,
                        "session_name": session.name if session else None,
                        "session_status": session.status if session else None,
                        "timestamp": capture["timestamp"],
                        "content": capture["text_content"],
                        "score": score,
                    }
                )

        # Apply ordering
        if order_by == "time":
            enriched.sort(key=lambda x: x["timestamp"], reverse=True)
        elif order_by == "session_name":
            enriched.sort(key=lambda x: (x["session_name"] or "", x["timestamp"]), reverse=True)
        elif order_by == "hybrid":
            # Hybrid: combine time and relevance (70% relevance, 30% recency)
            now = datetime.now()
            for item in enriched:
                timestamp = datetime.fromisoformat(item["timestamp"].replace('Z', '+00:00'))
                hours_old = (now - timestamp).total_seconds() / 3600
                time_score = max(0, 1 - hours_old / 168)  # Decay over 1 week
                item["hybrid_score"] = 0.7 * item["score"] + 0.3 * time_score
            enriched.sort(key=lambda x: x["hybrid_score"], reverse=True)
        else:  # relevance (default)
            enriched.sort(key=lambda x: x["score"], reverse=True)

        # Add attach information
        # Check which sessions are currently running
        running_sessions = set()
        try:
            # Get terminal manager instance to check running sessions
            from flashback_terminal.terminal import TerminalManager
            temp_manager = TerminalManager(self.db)
            running_sessions = set(temp_manager.sessions.keys())
        except Exception:
            pass  # If we can't check running status, continue without it
        
        for item in enriched:
            item["can_attach"] = (
                item["session_status"] in ("active", "running") and 
                item["session_uuid"] not in running_sessions
            )
            item["is_running"] = item["session_uuid"] in running_sessions

        return enriched[:limit]
    
    async def close(self) -> None:
        """Close all search components and cleanup resources."""
        if self.bm25:
            await self.bm25.close()
        # Embedding search doesn't need cleanup
