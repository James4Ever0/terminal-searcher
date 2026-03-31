"""BM25 text search for flashback."""

import math
import threading
from collections import defaultdict
from typing import Dict, List, Tuple
import sqlite3
import asyncio

from flashback.core.config import Config
from flashback.core.database import Database
from flashback.search.tokenizer import get_tokenizer

from flashback.core.logger import get_logger

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
    tqdm = None

# TODO: create persistant aiosqlite db index for bm25
# TODO: use conn.executemany for bulk update and insert.

logger = get_logger("search.bm25")

class BM25IndexDB:
    def __init__(self, db_path: str, readonly:bool=False):
        self.readonly=readonly
        self.db_path = db_path
        self.init_db()

    def _connect(self) -> sqlite3.Connection:
        """Create database connection."""
        if self.readonly:
            conn = sqlite3.connect("file:"+str(self.db_path)+"?mode=ro", uri=True, check_same_thread=False)
        else:
            conn = sqlite3.connect(self.db_path, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_db(self):
        with self._connect() as conn:
            # inverted index table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bm25_inverted_index (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id INTEGER NOT NULL,
                    term TEXT NOT NULL,
                    tf INTEGER NOT NULL,
                    UNIQUE(doc_id, term)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bm25_doc ON bm25_inverted_index(doc_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bm25_term ON bm25_inverted_index(term)")

            # doc lengths table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bm25_doc_lengths (
                    doc_id INTEGER PRIMARY KEY,
                    length INTEGER NOT NULL
                )
                """
            )

            # doc freqs table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bm25_doc_freqs (
                    term TEXT PRIMARY KEY,
                    df INTEGER NOT NULL
                )
                """
            )

            # corpus stats table (id shall be 1, always. only one entry stored in this table.)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bm25_corpus_stats (
                    id INTEGER PRIMARY KEY,
                    total_docs INTEGER NOT NULL,
                    avg_doc_length REAL NOT NULL
                )
                """
            )
    
    def load_stats(self) -> tuple[int, int]:
        """Load corpus statistics from database."""
        with self._connect() as conn:
            cursor = conn.execute("SELECT total_docs, avg_doc_length FROM bm25_corpus_stats WHERE id = 1")
            row = cursor.fetchone()
            if row:
                return row[0], row[1]
            return 0, 0
    
    def load_doc_lengths(self) -> Dict[int, int]:
        """Load document lengths from database."""
        with self._connect() as conn:
            cursor = conn.execute("SELECT doc_id, length FROM bm25_doc_lengths")
            return {row[0]: row[1] for row in cursor.fetchall()}
    
    def load_doc_freqs(self) -> Dict[str, int]:
        """Load document frequencies from database."""
        ret = defaultdict(int)
        with self._connect() as conn:
            cursor = conn.execute("SELECT term, df FROM bm25_doc_freqs")
            for row in cursor.fetchall():
                ret[row[0]] = row[1]
        return ret
    
    def load_inverted_index(self) -> Dict[str, Dict[int, int]]:
        """Load inverted index from database."""
        ret = defaultdict(dict)
        with self._connect() as conn:
            cursor = conn.execute("SELECT term, doc_id, tf FROM bm25_inverted_index")
            for row in cursor.fetchall():
                ret[row[0]][row[1]] = row[2]
        return ret
    
    def update_index_transactional(self, total_docs:int, avg_doc_length:float, doc_lengths:dict[int, int], invert_index:dict[str, dict[int, int]], doc_freqs:dict[str, int]):
        # update with rollback
        with self._connect() as conn:
            conn.execute("BEGIN")
            try:
                self._update_stats(conn, total_docs, avg_doc_length)
                self._update_invert_index(conn, invert_index)
                self._update_doc_lengths(conn, doc_lengths)
                self._update_doc_freqs(conn, doc_freqs)
                conn.execute("COMMIT")
            except Exception as e:
                conn.execute("ROLLBACK")
                raise e

    def _update_stats(self, conn:sqlite3.Connection, total_docs:int, avg_doc_length:float):
        """Update corpus statistics in database."""
        conn.execute("INSERT OR REPLACE INTO bm25_corpus_stats (id, total_docs, avg_doc_length) VALUES (1, ?, ?)", (total_docs, avg_doc_length))

    def _update_doc_lengths(self, conn:sqlite3.Connection, doc_lengths:dict[int, int]):
        conn.executemany("INSERT OR REPLACE INTO bm25_doc_lengths (doc_id, length) VALUES (?, ?)", doc_lengths.items())
    
    def _update_invert_index(self, conn:sqlite3.Connection, invert_index:dict[str, dict[int, int]]):
        conn.executemany("INSERT OR REPLACE INTO bm25_inverted_index (term, doc_id, tf) VALUES (?, ?, ?)", [(term, doc_id, tf) for term, doc_dict in invert_index.items() for doc_id, tf in doc_dict.items()])
    
    def _update_doc_freqs(self, conn:sqlite3.Connection, doc_freqs:dict[int, int]):
        conn.executemany("INSERT OR REPLACE INTO bm25_doc_freqs (term, df) VALUES (?, ?)", doc_freqs.items())

class BM25Search:
    """BM25 ranking for OCR text search."""

    def __init__(self, config: Config = None, db: Database = None):
        self.config = config or Config()
        self.db = db or Database(self.config.db_path, readonly=True)
        self.index_db = BM25IndexDB(self.config.bm25_index_db_path)
        self.k1 = self.config.get("search.bm25.k1", 1.5)
        self.b = self.config.get("search.bm25.b", 0.75)

        # Initialize tokenizer from config
        tokenizer_config = self.config.get("search.bm25.tokenizer", {})
        self.tokenizer = get_tokenizer(tokenizer_config)

        # Index data
        self.doc_lengths: Dict[int, int] = self.index_db.load_doc_lengths()
        self.N, self.avg_dl = self.index_db.load_stats()
        self.doc_freqs: Dict[str, int] = self.index_db.load_doc_freqs()

        self.inverted_index: Dict[str, Dict[int, int]] = self.index_db.load_inverted_index()

        self.rw_lock = threading.Lock()

        self._build_index()
    
    async def tokenize_single_document(self, doc_id:int, text:str, new_doc_lengths:dict[int, int], new_doc_tokens:dict[int, list[str]]):
        loop = asyncio.get_event_loop()
        tokens = await loop.run_in_executor(None, self._tokenize, text)
        new_doc_lengths[doc_id] = len(tokens)
        new_doc_tokens[doc_id] = tokens
    
    async def tokenize_documents(self, documents:dict[int, str], new_doc_lengths:dict[int, int], new_doc_tokens:dict[int, list[str]]):
        tasks = []
        for doc_id, text in documents.items():
            task = asyncio.create_task(self.tokenize_single_document(doc_id, text, new_doc_lengths, new_doc_tokens))
            tasks.append(task)
        await asyncio.gather(*tasks)

    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text into terms using configured tokenizer."""
        return self.tokenizer.tokenize(text)
    
    def refresh(self):
        self._build_index()

    def _build_index(self):
        """Build inverted index from database with stepwise logging."""
        logger.debug("[BM25 Index Build] Step 1/5: Connecting to database...")
        # Database connection is already established in __init__
        logger.debug("[BM25 Index Build] Step 2/5: Reading OCR data from database...")
        records = list(self.db.get_all_ocr_text())
        total_records = len(records)
        logger.debug(f"[BM25 Index Build] Loaded {total_records} records from database")

        logger.debug("[BM25 Index Build] Step 3/5: Iterating and get new documents")
        record_iterator = records
        old_doc_ids = set(self.doc_lengths.keys())

        # Use tqdm for progress bar if available
        if HAS_TQDM:
            record_iterator = tqdm(records, desc="BM25 Indexing", total=total_records, unit="docs")

        new_docs: dict[int, str] = dict()
        new_doc_ids : set[str] = set()
        
        for doc_id, text in record_iterator:
            if not text:
                # skip empty documents.
                continue
            # skip processed doc ids.
            if doc_id in old_doc_ids:
                continue
            elif doc_id in new_doc_ids:
                continue
            else:
                new_doc_ids.add(doc_id)
                new_docs[doc_id] = text
        
        logger.debug(f"[BM25 Index Build] Found {len(new_docs)} new records from database")
        
        self.update_documents(new_docs)

        logger.debug(f"[BM25 Index Build] Complete. Indexed {self.N} documents, avg_dl={self.avg_dl:.2f}")
    
    def update_documents(self, documents:dict[int, str]):
        """Update documents in the index."""
        with self.rw_lock:
            self._update_documents(documents)
    
    def _update_documents(self, documents:dict[int, str]):
        if not documents: return
        new_total_doc_lengths = 0

        new_doc_lengths: dict[int, int] = dict()
        new_doc_tokens: dict[int, list[str]] = defaultdict(list)

        new_doc_freqs: dict[str, int] = defaultdict(int)
        new_inverted_index: dict[str, dict[int, dict[int, int]]] = defaultdict(dict)

        updated_doc_freq_terms:set[str] = set()
        updated_invert_index_entries: set[tuple(str, int)]= set()
        updated_invert_index_terms: set[str] = set()

        asyncio.run(self.tokenize_documents(documents, new_doc_lengths, new_doc_tokens))

        new_total_doc_lengths = sum([len(tokens) for tokens in new_doc_tokens.values()])

        for doc_id, tokens in new_doc_tokens.items():
            # Count term frequencies
            term_counts: Dict[str, int] = defaultdict(int)
            for token in tokens:
                term_counts[token] += 1

            # Add to inverted index
            for term, freq in term_counts.items():
                new_inverted_index[term][doc_id] = freq
                updated_invert_index_entries.add((term, doc_id))
                new_doc_freqs[term] += 1
                updated_doc_freq_terms.add(term)
                updated_invert_index_terms.add(term)

        # update main in memory index

        old_total_doc_lengths = self.N * self.avg_dl
        self.avg_dl = (old_total_doc_lengths + new_total_doc_lengths) / (self.N + len(new_doc_lengths))

        self.doc_lengths.update(new_doc_lengths)
        self.N += len(new_doc_lengths)

        for term in updated_invert_index_terms:
            self.inverted_index[term].update( new_inverted_index[term])

        doc_freqs_for_db_update = dict()

        for term, freq in new_doc_freqs.items():
            self.doc_freqs[term] += freq
            doc_freqs_for_db_update[term] = self.doc_freqs[term]

        # batch persist to search index db.
        self.index_db.update_index_transactional(
            total_docs=self.N,
            avg_doc_length=self.avg_dl,
            doc_lengths=new_doc_lengths,
            invert_index=new_inverted_index,
            doc_freqs=doc_freqs_for_db_update
        )
    
    def search(self, query: str, top_k:int=20) -> List[Tuple[int, float]]:
        """Search for query and return ranked document IDs with scores."""
        with self.rw_lock:
            return self._search(query, top_k)
    
    def _search(self, query: str, top_k: int = 20) -> List[Tuple[int, float]]:
        query_terms = self._tokenize(query)
        scores: Dict[int, float] = defaultdict(float)

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
        return results[:top_k]
