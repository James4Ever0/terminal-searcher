"""Async BM25 index for terminal search functionality."""

import aiosqlite
import asyncio
import math
import threading
from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

from flashback_terminal.logger import logger


class BM25SQLiteIndexAsync:
    """
    An async BM25 index implementation with SQLite persistence for terminal search.
    
    This class provides BM25 ranking functionality with async operations, document ID tracking,
    and persistent storage using SQLite. It supports incremental addition, batch operations,
    and efficient querying with BM25 scoring.
    
    Parameters
    ----------
    db_path : str
        Path to the SQLite database file for index storage.
    k1 : float, optional
        BM25 parameter k1 (default 1.5).
    b : float, optional
        BM25 parameter b (default 0.75).
    tokenizer: Callable[[str], list[str]], optional
        Function to tokenize text into terms (default None, uses simple whitespace split).
    """

    def __init__(
        self,
        db_path: str,
        k1: float = 1.5,
        b: float = 0.75,
        tokenizer: Optional[Callable[str, list[str]]] = None
    ):
        self.db_path = Path(db_path)
        self.k1 = k1
        self.b = b

        if tokenizer:
            self.tokenizer = tokenizer
        else:
            self.tokenizer = self._tokenize
        
        # Index data structures
        self.doc_lengths: Dict[str, int] = {}
        self.doc_freqs: Dict[str, int] = {}
        self.inverted_index: Dict[str, Dict[str, int]] = {}
        self.N = 0  # Number of documents
        self.avg_dl = 0.0  # Average document length
        
        # Threading locks
        self.index_lock = threading.Lock()
        self.update_lock = threading.Lock()

    async def initialize(self) -> None:
        """Initialize the SQLite database and load existing index data."""
        # Create database directory if it doesn't exist
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize database schema
        await self._init_db()
        
        # Load existing index data
        await self._load_index_data()
        
        logger.debug(f"[BM25SQLiteIndexAsync] Initialized index at {self.db_path} with {self.N} documents")

    async def _init_db(self) -> None:
        """Initialize SQLite database schema."""
        async with aiosqlite.connect(str(self.db_path)) as conn:
            # Inverted index table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS bm25_inverted_index (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id TEXT NOT NULL,
                    term TEXT NOT NULL,
                    tf INTEGER NOT NULL,
                    UNIQUE(doc_id, term)
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_bm25_doc ON bm25_inverted_index(doc_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_bm25_term ON bm25_inverted_index(term)")

            # Document lengths table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS bm25_doc_lengths (
                    doc_id TEXT PRIMARY KEY,
                    length INTEGER NOT NULL
                )
            """)

            # Document frequencies table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS bm25_doc_freqs (
                    term TEXT PRIMARY KEY,
                    df INTEGER NOT NULL
                )
            """)

            # Corpus statistics table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS bm25_corpus_stats (
                    id INTEGER PRIMARY KEY,
                    total_docs INTEGER NOT NULL,
                    avg_doc_length REAL NOT NULL
                )
            """)
            await conn.commit()

    async def _load_index_data(self) -> None:
        """Load index data from SQLite database."""
        async with aiosqlite.connect(str(self.db_path)) as conn:
            # Load corpus statistics
            cursor = await conn.execute("SELECT total_docs, avg_doc_length FROM bm25_corpus_stats WHERE id = 1")
            row = await cursor.fetchone()
            if row:
                self.N, self.avg_dl = row
            else:
                self.N, self.avg_dl = 0, 0.0

            # Load document lengths
            cursor = await conn.execute("SELECT doc_id, length FROM bm25_doc_lengths")
            rows = await cursor.fetchall()
            self.doc_lengths = {row[0]: row[1] for row in rows}

            # Load document frequencies
            cursor = await conn.execute("SELECT term, df FROM bm25_doc_freqs")
            rows = await cursor.fetchall()
            self.doc_freqs = {row[0]: row[1] for row in rows}

            # Load inverted index
            cursor = await conn.execute("SELECT term, doc_id, tf FROM bm25_inverted_index")
            rows = await cursor.fetchall()
            self.inverted_index = defaultdict(dict)
            for row in rows:
                term, doc_id, tf = row
                self.inverted_index[term][doc_id] = tf

    def exists(self, doc_id: Union[str, int]) -> bool:
        """Check if a document with the given ID is already indexed."""
        doc_id = str(doc_id)
        return doc_id in self.doc_lengths

    async def add_document(self, doc_id: Union[str, int], text: str) -> None:
        """
        Add a single document to the index.
        
        Parameters
        ----------
        doc_id : str or int
            Unique identifier for the document.
        text : str
            Document content.
        """
        await self.add_documents([(doc_id, text)])

    async def add_documents(self, documents: List[Tuple[Union[str, int], str]]) -> None:
        """
        Add multiple documents to the index in a batch operation.
        
        Parameters
        ----------
        documents : list of (doc_id, text)
            List of document tuples to add.
        """
        if not documents:
            return
        
        with self.update_lock:
            # Process documents
            new_doc_lengths: Dict[str, int] = {}
            new_doc_tokens: Dict[str, List[str]] = {}
            new_doc_freqs: Dict[str, int] = defaultdict(int)
            new_inverted_index: Dict[str, Dict[str, int]] = defaultdict(dict)

            # Tokenize documents
            await self._tokenize_documents(documents, new_doc_lengths, new_doc_tokens)

            # Build inverted index
            for doc_id, tokens in new_doc_tokens.items():
                term_counts: Dict[str, int] = defaultdict(int)
                for token in tokens:
                    term_counts[token] += 1

                for term, freq in term_counts.items():
                    new_inverted_index[term][doc_id] = freq
                    new_doc_freqs[term] += 1

            # Update in-memory index
            await self._update_index(new_doc_lengths, new_doc_freqs, new_inverted_index)

            # Persist to database
            await self._persist_updates(new_doc_lengths, new_doc_freqs, new_inverted_index)

            logger.debug(f"[BM25SQLiteIndexAsync] Added {len(new_doc_lengths)} new documents to index")

    async def _tokenize_documents(
        self, 
        documents: List[Tuple[Union[str, int], str]], 
        new_doc_lengths: Dict[str, int], 
        new_doc_tokens: Dict[str, List[str]]
    ) -> None:
        """Tokenize documents asynchronously."""
        loop = asyncio.get_event_loop()
        tasks = []
        
        for doc_id, text in documents:
            doc_id_str = str(doc_id)
            if doc_id_str in self.doc_lengths:
                continue  # Skip existing documents
            
            task = loop.run_in_executor(None, self._tokenize_single, doc_id_str, text, new_doc_lengths, new_doc_tokens)
            tasks.append(task)
        
        if tasks:
            await asyncio.gather(*tasks)

    def _tokenize_single(self, doc_id: str, text: str, new_doc_lengths: Dict[str, int], new_doc_tokens: Dict[str, List[str]]) -> None:
        """Tokenize a single document."""
        tokens = self.tokenizer(text)
        new_doc_lengths[doc_id] = len(tokens)
        new_doc_tokens[doc_id] = tokens

    def _tokenize(self, text: str) -> List[str]:
        """Simple tokenization - split on whitespace and lowercase."""
        return [token.lower() for token in text.split() if token.strip()]

    async def _update_index(
        self, 
        new_doc_lengths: Dict[str, int], 
        new_doc_freqs: Dict[str, int], 
        new_inverted_index: Dict[str, Dict[str, int]]
    ) -> None:
        """Update in-memory index structures."""
        if not new_doc_lengths:
            return

        with self.index_lock:
            # Update document count and average length
            old_total_length = self.N * self.avg_dl
            new_total_length = sum(new_doc_lengths.values())
            self.N += len(new_doc_lengths)
            self.avg_dl = (old_total_length + new_total_length) / self.N if self.N > 0 else 0.0

            # Update document lengths
            self.doc_lengths.update(new_doc_lengths)

            # Update document frequencies
            for term, freq in new_doc_freqs.items():
                self.doc_freqs[term] = self.doc_freqs.get(term, 0) + freq

            # Update inverted index
            for term, doc_dict in new_inverted_index.items():
                if term not in self.inverted_index:
                    self.inverted_index[term] = {}
                self.inverted_index[term].update(doc_dict)

    async def _persist_updates(
        self, 
        new_doc_lengths: Dict[str, int], 
        new_doc_freqs: Dict[str, int], 
        new_inverted_index: Dict[str, Dict[str, int]]
    ) -> None:
        """Persist index updates to SQLite database."""
        async with aiosqlite.connect(str(self.db_path)) as conn:
            await conn.execute("BEGIN")
            try:
                # Update corpus statistics
                await conn.execute(
                    "INSERT OR REPLACE INTO bm25_corpus_stats (id, total_docs, avg_doc_length) VALUES (1, ?, ?)",
                    (self.N, self.avg_dl)
                )

                # Update document lengths
                if new_doc_lengths:
                    await conn.executemany(
                        "INSERT OR REPLACE INTO bm25_doc_lengths (doc_id, length) VALUES (?, ?)",
                        [(doc_id, length) for doc_id, length in new_doc_lengths.items()]
                    )

                # Update document frequencies
                if new_doc_freqs:
                    await conn.executemany(
                        "INSERT OR REPLACE INTO bm25_doc_freqs (term, df) VALUES (?, ?)",
                        [(term, self.doc_freqs[term]) for term in new_doc_freqs.keys()]
                    )

                # Update inverted index
                if new_inverted_index:
                    index_updates = [
                        (term, doc_id, tf)
                        for term, doc_dict in new_inverted_index.items()
                        for doc_id, tf in doc_dict.items()
                    ]
                    await conn.executemany(
                        "INSERT OR REPLACE INTO bm25_inverted_index (term, doc_id, tf) VALUES (?, ?, ?)",
                        index_updates
                    )

                await conn.commit()
            except Exception as e:
                await conn.rollback()
                raise e

    async def query(self, query_text: str, top_n: int = 10) -> List[Tuple[str, float]]:
        """
        Search the index with a query and return the top matching documents.
        
        Parameters
        ----------
        query_text : str
            The query string.
        top_n : int, optional
            Number of top results to return (default 10).
            
        Returns
        -------
        list of (doc_id, score)
            Sorted list of document IDs and their BM25 scores, highest first.
        """
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, self._query, query_text, top_n)
        return results

    def _query(self, query_text: str, top_n: int = 10) -> List[Tuple[str, float]]:
        """Synchronous BM25 query implementation."""
        if self.N == 0:
            return []

        query_terms = self._tokenize(query_text)
        scores: Dict[str, float] = defaultdict(float)

        for term in query_terms:
            if term not in self.inverted_index:
                continue

            df = self.doc_freqs[term]
            idf = math.log((self.N - df + 0.5) / (df + 0.5) + 1)

            for doc_id, tf in self.inverted_index[term].items():
                dl = self.doc_lengths.get(doc_id, 0)
                if dl == 0:
                    continue

                # BM25 formula
                denom = self.k1 * (1 - self.b + self.b * (dl / self.avg_dl)) + tf
                score = idf * (tf * (self.k1 + 1)) / denom
                scores[doc_id] += score

        # Sort by score descending
        results = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return results[:top_n]

    async def clear_all(self) -> None:
        """Clear all documents from the index."""
        with self.update_lock:
            async with aiosqlite.connect(str(self.db_path)) as conn:
                await conn.execute("BEGIN")
                try:
                    await conn.execute("DELETE FROM bm25_inverted_index")
                    await conn.execute("DELETE FROM bm25_doc_lengths")
                    await conn.execute("DELETE FROM bm25_doc_freqs")
                    await conn.execute("DELETE FROM bm25_corpus_stats")
                    await conn.commit()
                except Exception as e:
                    await conn.rollback()
                    raise e

            # Clear in-memory data
            with self.index_lock:
                self.doc_lengths.clear()
                self.doc_freqs.clear()
                self.inverted_index.clear()
                self.N = 0
                self.avg_dl = 0.0

            logger.debug("[BM25SQLiteIndexAsync] Cleared all documents from index")

    async def close(self) -> None:
        """Close the index and cleanup resources."""
        logger.debug("[BM25SQLiteIndexAsync] Index closed")

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    @property
    def num_docs(self) -> int:
        """Get the number of documents in the index."""
        return self.N
