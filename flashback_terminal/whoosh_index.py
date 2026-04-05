"""Async Whoosh index for terminal search functionality."""

import aiosqlite
import asyncio
import threading
from pathlib import Path
from typing import List, Tuple, Union, Set

try:
    from jieba.analyse.analyzer import ChineseAnalyzer
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False

try:
    from whoosh import fields, index, writing
    from whoosh.analysis import StandardAnalyzer
    from whoosh.qparser import QueryParser
    from whoosh.query import Term
    WHOOSH_AVAILABLE = True
except ImportError:
    WHOOSH_AVAILABLE = False

from flashback_terminal.logger import logger


def get_analyzer(use_chinese: bool = False):
    """Get appropriate analyzer based on language preference."""
    if use_chinese:
        if JIEBA_AVAILABLE:
            return ChineseAnalyzer()
        else:
            logger.warning("Chinese analyzer requested but jieba not available, using StandardAnalyzer")
            return StandardAnalyzer()
    else:
        return StandardAnalyzer()


class WhooshIndexAsync:
    """
    An async wrapper around Whoosh full-text search index with support for
    unique document IDs, incremental addition, batch operations, and efficient querying.
    
    The index maintains a set of document IDs for fast existence checking
    and supports both individual and batch commit operations.

    Parameters
    ----------
    index_dir : str
        Path to the Whoosh index directory.
    use_chinese : bool, optional
        Whether to use Chinese analyzer with jieba tokenization (default False).
    """

    def __init__(
        self,
        index_dir: str,
        use_chinese: bool = False
    ):
        if not WHOOSH_AVAILABLE:
            raise ImportError("Whoosh is required. Install with: pip install whoosh")
            
        self.index_dir = Path(index_dir)
        self.use_chinese = use_chinese
        self.index = None
        self.writer = None
        self.searcher = None
        
        # SQLite database for document ID tracking
        self.doc_ids_db = self.index_dir / "doc_ids.db"
        self._doc_ids: Set[str] = set()
        
        # Threading lock for Whoosh operations
        self.index_lock = threading.Lock()
        self.update_lock = threading.Lock()
        
        # Schema definition
        self.schema = fields.Schema(
            doc_id=fields.ID(stored=True, unique=True),
            session_id=fields.ID(stored=True),
            content=fields.TEXT(stored=True, analyzer=get_analyzer(use_chinese))
        )

    async def initialize(self) -> None:
        """Initialize the Whoosh index and load existing document IDs."""
        # Create index directory if it doesn't exist
        self.index_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize SQLite database for document IDs
        await self._init_doc_ids_db()
        
        # Initialize or open the index
        with self.index_lock:
            if index.exists_in(str(self.index_dir)):
                self.index = index.open_dir(str(self.index_dir))
                await self._load_doc_ids()
            else:
                self.index = index.create_in(str(self.index_dir), self.schema)
        
        logger.debug(f"[WhooshIndexAsync] Initialized index at {self.index_dir} with {len(self._doc_ids)} documents")

    async def _init_doc_ids_db(self) -> None:
        """Initialize SQLite database for document ID tracking."""
        async with aiosqlite.connect(str(self.doc_ids_db)) as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS doc_ids (
                    doc_id TEXT PRIMARY KEY
                )
            """)
            await conn.commit()

    async def _load_doc_ids(self) -> None:
        """Load document IDs from SQLite database."""
        async with aiosqlite.connect(str(self.doc_ids_db)) as conn:
            async with conn.execute("SELECT doc_id FROM doc_ids") as cursor:
                rows = await cursor.fetchall()
                self._doc_ids = {row[0] for row in rows}

    async def _save_doc_ids(self, new_doc_ids: Set[str]) -> None:
        """Save new document IDs to SQLite database in batch."""
        if not new_doc_ids:
            return
            
        async with aiosqlite.connect(str(self.doc_ids_db)) as conn:
            # Use executemany for batch insert
            await conn.executemany(
                "INSERT OR IGNORE INTO doc_ids (doc_id) VALUES (?)",
                [(doc_id,) for doc_id in new_doc_ids]
            )
            await conn.commit()

    def exists(self, doc_id: Union[str, int]) -> bool:
        """Check if a document with the given ID is already indexed."""
        doc_id = str(doc_id)
        return doc_id in self._doc_ids

    async def add_document(self, doc_id: Union[str, int], session_id:int, text: str) -> None:
        """
        Add a single document to the index.
        
        Parameters
        ----------
        doc_id : str or int
            Unique identifier for the document.
        session_id: int
            Terminal session id.
        text : str
            Document content.
        """
        await self.add_documents([(doc_id, session_id, text)])

    async def add_documents(self, documents: List[Tuple[Union[str, int], int, str]]) -> None:
        """
        Add multiple documents to the index in a batch operation.
        
        Parameters
        ----------
        documents : list of (doc_id, session_id, text)
            List of document tuples to add.
        """
        if not documents:
            return
        
        def _add_docs():
            with self.index_lock:
                # Use a writer for batch operations
                writer = writing.AsyncWriter(self.index)
                added_any = False
                newly_added_doc_ids = set()
                
                for doc_id, session_id, text in documents:
                    doc_id_str = str(doc_id)
                    session_id_str = str(session_id)
                    
                    # Skip if document already exists
                    if doc_id_str in self._doc_ids:
                        continue
                    
                    try:
                        writer.add_document(doc_id=doc_id_str, session_id=session_id_str, content=text)
                        self._doc_ids.add(doc_id_str)
                        newly_added_doc_ids.add(doc_id_str)
                        added_any = True
                    except Exception as e:
                        logger.warning(f"[WhooshIndexAsync] Failed to add document {doc_id}: {e}")
                
                if added_any:
                    writer.commit()
                    return newly_added_doc_ids
                else:
                    writer.cancel()
                    return set()
        with self.update_lock:
            # Run the synchronous Whoosh operations in executor
            new_doc_ids = await asyncio.get_event_loop().run_in_executor(None, _add_docs)
            
            # Save newly added document IDs to SQLite database
            if new_doc_ids:
                await self._save_doc_ids(new_doc_ids)
                logger.debug(f"[WhooshIndexAsync] Added {len(new_doc_ids)} new documents to index")
    
    async def query(self, query_text:str, top_n:int = 10, filter_ids: list[int]=[], doc_ids: list[int] = []) -> List[Tuple[str, float]]:
        """
        Search the index with a query and return the top matching documents.
        
        Parameters
        ----------
        query_text : str
            The query string.
        top_n : int, optional
            Number of top results to return (default 10).
        filter_ids: list[int], optional
            Session IDs to include in the search result.
        doc_ids: list[int], optional
            Document IDs to include in the search result.
            
        Returns
        -------
        list of (doc_id, score)
            Sorted list of document IDs and their relevance scores, highest first.
        """
        loop = asyncio.get_event_loop()
        ret = await loop.run_in_executor(None, self._query, query_text, top_n, filter_ids, doc_ids)
        return ret

    def _query(self, query_text: str, top_n: int = 10, filter_ids: list[int]=[], doc_ids: list[int]=[]) -> List[Tuple[str, float]]:
        """Synchronized query"""
        if not self.index:
            return []
        
        with self.index_lock:
            searcher = self.index.searcher()
            parser = QueryParser("content", self.schema)
            query = parser.parse(query_text)
            
            query_filter = None

            if filter_ids:
                # join with "or"
                for _id in filter_ids:
                    id_str = str(_id)
                    if query_filter is None:
                        query_filter = Term("session_id", id_str)
                    else:
                        query_filter = query_filter | Term("session_id", id_str)
            
            if doc_ids:
                # join with "or"
                for _id in doc_ids:
                    id_str = str(_id)
                    if query_filter is None:
                        query_filter = Term("doc_id", id_str)
                    else:
                        query_filter = query_filter | Term("doc_id", id_str)
            
            if query_filter is not None:
                results = searcher.search(query, limit=top_n, filter=query_filter)
            else:
                results = searcher.search(query, limit=top_n)
            
            # Convert to (doc_id, score) tuples
            return [(hit['doc_id'], hit.score) for hit in results]

    async def clear_all(self) -> None:
        """Clear all documents from the index."""
        def _clear():
            with self.index_lock:
                # Clear the index
                writer = writing.AsyncWriter(self.index)
                writer.commit(mergetype=writing.CLEAR)
        
        with self.update_lock:
            # Clear document IDs from SQLite
            async with aiosqlite.connect(str(self.doc_ids_db)) as conn:
                await conn.execute("DELETE FROM doc_ids")
                await conn.commit()
            
            await asyncio.get_event_loop().run_in_executor(None, _clear)
            
            # Clear in-memory document IDs
            self._doc_ids.clear()
            logger.debug("[WhooshIndexAsync] Cleared all documents from index")

    async def close(self) -> None:
        """Close the index and cleanup resources."""
        if self.searcher:
            self.searcher.close()
            self.searcher = None
        
        # Whoosh index doesn't need explicit closing in most cases
        logger.debug("[WhooshIndexAsync] Index closed")

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    @property
    def num_docs(self) -> int:
        """Get the number of documents in the index."""
        return len(self._doc_ids)
