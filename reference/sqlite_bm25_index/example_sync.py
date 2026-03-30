import sqlite3
import math
import re
from collections import defaultdict, Counter
from typing import List, Tuple, Optional, Callable, Union

class BM25SQLiteIndex:
    """
    A BM25 index stored in SQLite with support for unique document IDs,
    incremental addition, and efficient querying.

    The index is loaded into memory on initialization for fast scoring.
    New documents are immediately persisted to the database.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file.
    tokenizer : callable, optional
        A function that takes a string and returns a list of tokens.
        Default is a simple tokenizer that splits on whitespace, lowercases,
        and removes punctuation.
    k1 : float, optional
        BM25 term frequency saturation parameter (default 1.5).
    b : float, optional
        BM25 document length normalization parameter (default 0.75).
    """

    def __init__(
        self,
        db_path: str,
        tokenizer: Optional[Callable[[str], List[str]]] = None,
        k1: float = 1.5,
        b: float = 0.75
    ):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.tokenizer = tokenizer or self._default_tokenizer
        self.k1 = k1
        self.b = b

        # In-memory structures
        self.doc_ids: List[str] = []          # list of document IDs
        self.doc_lengths: List[int] = []      # list of document lengths (number of tokens)
        self.doc_id_to_index: dict = {}       # mapping from doc_id to index in the lists
        self.term_postings: dict = defaultdict(list)  # term -> list of (doc_index, tf)
        self.term_df: dict = {}               # term -> document frequency

        self.num_docs = 0
        self.total_length = 0
        self.avgdl = 0.0

        self._create_tables()
        self._load_from_db()

    def _default_tokenizer(self, text: str) -> List[str]:
        """Simple tokenizer: lowercases, splits on whitespace, removes punctuation."""
        text = re.sub(r'[^\w\s]', '', text.lower())
        return text.split()

    def _create_tables(self) -> None:
        """Create the necessary tables if they do not exist."""
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                length INTEGER NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS terms (
                term TEXT PRIMARY KEY,
                df INTEGER NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS postings (
                term TEXT,
                doc_id TEXT,
                tf INTEGER NOT NULL,
                PRIMARY KEY (term, doc_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        self.conn.commit()

    def _load_from_db(self) -> None:
        """Load all index data from SQLite into memory."""
        cursor = self.conn.cursor()

        # Load documents
        cursor.execute("SELECT doc_id, length FROM documents")
        rows = cursor.fetchall()
        self.doc_ids = [row["doc_id"] for row in rows]
        self.doc_lengths = [row["length"] for row in rows]
        self.doc_id_to_index = {doc_id: i for i, doc_id in enumerate(self.doc_ids)}
        self.num_docs = len(self.doc_ids)
        self.total_length = sum(self.doc_lengths)
        self.avgdl = self.total_length / self.num_docs if self.num_docs > 0 else 0.0

        # Load postings and build term_df
        cursor.execute("SELECT term, doc_id, tf FROM postings")
        rows = cursor.fetchall()
        for row in rows:
            term, doc_id, tf = row["term"], row["doc_id"], row["tf"]
            idx = self.doc_id_to_index[doc_id]
            self.term_postings[term].append((idx, tf))
        # Compute term_df from the loaded postings
        for term, postings in self.term_postings.items():
            self.term_df[term] = len(set(idx for idx, _ in postings))

    def exists(self, doc_id: Union[str, int]) -> bool:
        """Check if a document with the given ID is already indexed."""
        doc_id = str(doc_id)
        return doc_id in self.doc_id_to_index

    def add_document(self, doc_id: Union[str, int], text: str) -> None:
        """
        Add a new document to the index.

        Parameters
        ----------
        doc_id : str or int
            Unique identifier for the document. Will be stored as string.
        text : str
            Document content.

        Raises
        ------
        ValueError
            If a document with the same ID already exists.
        """
        doc_id = str(doc_id)
        if self.exists(doc_id):
            raise ValueError(f"Document with ID '{doc_id}' already exists in the index.")

        # Tokenize and compute term frequencies
        tokens = self.tokenizer(text)
        length = len(tokens)
        if length == 0:
            # Empty document: still add but no terms
            self._insert_empty_document(doc_id)
            return

        tf_counter = Counter(tokens)

        # Prepare data for insertion
        new_index = self.num_docs
        # Update in-memory structures
        self.doc_ids.append(doc_id)
        self.doc_lengths.append(length)
        self.doc_id_to_index[doc_id] = new_index
        for term, tf in tf_counter.items():
            self.term_postings[term].append((new_index, tf))
            # Update term_df: if this is the first occurrence of the term in the new doc
            # But since we just added one doc, we increment df by 1 if term was already seen,
            # otherwise set to 1. We need to know if the term was present before this doc.
            if term in self.term_df:
                self.term_df[term] += 1
            else:
                self.term_df[term] = 1

        # Update global statistics
        self.num_docs += 1
        self.total_length += length
        self.avgdl = self.total_length / self.num_docs

        # Persist to SQLite (in a transaction)
        cursor = self.conn.cursor()
        try:
            cursor.execute("BEGIN TRANSACTION")

            # Insert document
            cursor.execute(
                "INSERT INTO documents (doc_id, length) VALUES (?, ?)",
                (doc_id, length)
            )

            # Insert/update terms and postings
            for term, tf in tf_counter.items():
                # UPSERT for term document frequency
                cursor.execute("""
                    INSERT INTO terms (term, df) VALUES (?, 1)
                    ON CONFLICT(term) DO UPDATE SET df = df + 1
                """, (term,))
                # Insert posting
                cursor.execute(
                    "INSERT INTO postings (term, doc_id, tf) VALUES (?, ?, ?)",
                    (term, doc_id, tf)
                )

            # Update metadata
            cursor.execute(
                "INSERT INTO meta (key, value) VALUES ('num_docs', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = ?",
                (str(self.num_docs), str(self.num_docs))
            )
            cursor.execute(
                "INSERT INTO meta (key, value) VALUES ('total_length', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = ?",
                (str(self.total_length), str(self.total_length))
            )

            cursor.execute("COMMIT")
        except Exception:
            self.conn.rollback()
            # Rollback in-memory changes
            self.doc_ids.pop()
            self.doc_lengths.pop()
            del self.doc_id_to_index[doc_id]
            for term, tf in tf_counter.items():
                self.term_postings[term].pop()  # remove last element
                # If this term's posting list becomes empty, delete key? Not necessary.
                self.term_df[term] -= 1
                if self.term_df[term] == 0:
                    del self.term_df[term]
            self.num_docs -= 1
            self.total_length -= length
            self.avgdl = self.total_length / self.num_docs if self.num_docs > 0 else 0.0
            raise

    def _insert_empty_document(self, doc_id: str) -> None:
        """Handle insertion of an empty document (no terms)."""
        new_index = self.num_docs
        self.doc_ids.append(doc_id)
        self.doc_lengths.append(0)
        self.doc_id_to_index[doc_id] = new_index
        self.num_docs += 1
        # total_length unchanged (0 added)
        self.avgdl = self.total_length / self.num_docs

        cursor = self.conn.cursor()
        cursor.execute("BEGIN TRANSACTION")
        cursor.execute(
            "INSERT INTO documents (doc_id, length) VALUES (?, ?)",
            (doc_id, 0)
        )
        cursor.execute(
            "INSERT INTO meta (key, value) VALUES ('num_docs', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = ?",
            (str(self.num_docs), str(self.num_docs))
        )
        cursor.execute(
            "INSERT INTO meta (key, value) VALUES ('total_length', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = ?",
            (str(self.total_length), str(self.total_length))
        )
        cursor.execute("COMMIT")

    def query(self, query_text: str, top_n: int = 10) -> List[Tuple[str, float]]:
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
        if self.num_docs == 0:
            return []

        query_tokens = self.tokenizer(query_text)
        if not query_tokens:
            return []

        scores = [0.0] * self.num_docs

        # Compute IDF for each query term
        for term in set(query_tokens):
            if term not in self.term_df:
                continue
            df = self.term_df[term]
            idf = math.log((self.num_docs - df + 0.5) / (df + 0.5) + 1.0)

            # Get postings for this term
            postings = self.term_postings.get(term, [])
            for doc_idx, tf in postings:
                doc_len = self.doc_lengths[doc_idx]
                # BM25 component
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (1 - self.b + self.b * (doc_len / self.avgdl))
                scores[doc_idx] += idf * (numerator / denominator)

        # Get top_n results
        indexed_scores = list(enumerate(scores))
        indexed_scores.sort(key=lambda x: x[1], reverse=True)
        top_results = [(self.doc_ids[idx], score) for idx, score in indexed_scores[:top_n] if score > 0]
        return top_results

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# Example usage
if __name__ == "__main__":
    # Create a new index (or load existing)
    index = BM25SQLiteIndex("bm25_example.db")

    # Add some documents
    docs = [
        ("doc1", "The quick brown fox jumps over the lazy dog"),
        ("doc2", "A quick brown dog jumps over a lazy fox"),
        ("doc3", "The lazy dog sleeps under the tree"),
    ]
    for doc_id, text in docs:
        if not index.exists(doc_id):
            index.add_document(doc_id, text)

    # Query
    results = index.query("quick fox")
    for doc_id, score in results:
        print(f"{doc_id}: {score:.4f}")

    index.close()