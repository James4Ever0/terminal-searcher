"""Search functionality for flashback-terminal."""

import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from flashback_terminal.logger import logger

try:
    # TODO: use jieba instead of nltk.
    import jieba

    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False

from flashback_terminal.config import get_config
from flashback_terminal.database import Database


class BM25Search:
    """BM25 text search over terminal output."""

    def __init__(self, db: Database):
        self.db = db
        self.config = get_config()
        self.k1 = self.config.get("search.bm25.k1", 1.5)
        self.b = self.config.get("search.bm25.b", 0.75)
        self._build_index()

    def _tokenize(self, text: str) -> List[str]:
        if JIEBA_AVAILABLE:
            return list(jieba.cut(text.lower()))
        return re.findall(r"\b\w+\b", text.lower())

    def _build_index(self) -> None:
        with self.db._connect() as conn:
            rows = conn.execute("SELECT id, session_id, content FROM terminal_output").fetchall()
        
        logger.debug(f"[BM25Search] Building index from {len(rows)} terminal output records")

        self.documents: Dict[int, Dict] = {}
        self.doc_lengths: Dict[int, int] = {}
        self.inverted_index: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
        self.doc_freqs: Dict[str, int] = defaultdict(int)

        total_length = 0

        for row in rows:
            doc_id = row["id"]
            content = row["content"]

            tokens = self._tokenize(content)
            self.documents[doc_id] = {"session_id": row["session_id"], "content": content}
            self.doc_lengths[doc_id] = len(tokens)
            total_length += len(tokens)

            term_counts: Dict[str, int] = defaultdict(int)
            for token in tokens:
                term_counts[token] += 1

            for term, freq in term_counts.items():
                self.inverted_index[term].append((doc_id, freq))
                self.doc_freqs[term] += 1

        self.N = len(self.documents)
        self.avg_dl = total_length / self.N if self.N > 0 else 0
        logger.debug(f"[BM25Search] Index built: {self.N} documents, avg length={self.avg_dl:.2f}")

    def search(
        self, query: str, session_ids: Optional[List[int]] = None, top_k: int = 50
    ) -> List[Tuple[int, float]]:
        query_terms = self._tokenize(query)
        scores: Dict[int, float] = defaultdict(float)

        for term in query_terms:
            if term not in self.inverted_index:
                continue

            df = self.doc_freqs[term]
            idf = math.log((self.N - df + 0.5) / (df + 0.5) + 1)

            for doc_id, tf in self.inverted_index[term]:
                if session_ids and self.documents[doc_id]["session_id"] not in session_ids:
                    continue

                dl = self.doc_lengths[doc_id]
                denom = self.k1 * (1 - self.b + self.b * (dl / self.avg_dl)) + tf
                score = idf * (tf * (self.k1 + 1)) / denom
                scores[doc_id] += score

        results = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return results[:top_k]


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

    def search(
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
                session = self.db.get_session_by_uuid(session_uuid)
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

    def search(
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
            self.bm25._build_index()
            results = self.bm25.search(query, session_ids, limit)

        elif mode == "semantic":
            if not self.embedding:
                return []
            results = self.embedding.search(query, session_ids, limit)

        elif mode == "hybrid":
            bm25_results = []
            embedding_results = []

            if self.bm25:
                self.bm25._build_index()
                bm25_results = self.bm25.search(query, session_ids, limit * 2)
            if self.embedding:
                embedding_results = self.embedding.search(query, session_ids, limit * 2)

            rrf_k = self.config.get("modules.semantic_search.rrf_k", 60)
            results = reciprocal_rank_fusion(bm25_results, embedding_results, k=rrf_k, top_k=limit)

        else:
            raise ValueError(f"Unknown search mode: {mode}")

        enriched = []
        for doc_id, score in results:
            output = self.db.get_terminal_output_by_id(doc_id)
            if output:
                session = self.db.get_session(output.session_id)
                
                # Apply time range filter if specified
                if time_range:
                    timestamp = output.timestamp
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
                        "output_id": doc_id,
                        "session_id": output.session_id,
                        "session_uuid": session.uuid if session else None,
                        "session_name": session.name if session else None,
                        "session_status": session.status if session else None,
                        "sequence_num": output.sequence_num,
                        "timestamp": output.timestamp.isoformat(),
                        "content": output.content,
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
