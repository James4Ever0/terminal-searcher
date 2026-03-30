Yes, there are several third-party Python libraries that provide BM25 search with SQLite persistence. Based on your requirements—SQLite-backed, unique document ID constraints, existence checking, and persistence—here are the most relevant options:

## Top Recommendations

### 1. **sqlitesearch** (closest to your custom implementation)
- **PyPI**: `sqlitesearch` 
- **Description**: A tiny, SQLite-backed search library specifically designed for small, local projects. Uses SQLite FTS5 with BM25 ranking .

**Key Features:**
- Persistent SQLite storage (single file)
- Text search using SQLite FTS5 with BM25 ranking
- Customizable ID field (`id_field` parameter)
- `add()` method for single document insertion
- `clear()` method for index reset
- Optional stemming support
- Filtering by keyword fields

**Example:**
```python
from sqlitesearch import TextSearchIndex

# Create index with custom ID field
index = TextSearchIndex(
    text_fields=["title", "description"],
    id_field="doc_id",  # Your document ID field
    db_path="search.db"
)

# Check existence via try/except or by querying
index.fit(documents)  # Only if index is empty
index.add({"doc_id": "123", "title": "..."})  # Add single document

# Search with BM25
results = index.search("python", output_ids=True)
```

**Limitations**: Does not have an explicit `exists()` method in the documentation, but you can check existence by attempting to retrieve or handle integrity errors .

---

### 2. **sqlalchemy-vectorstores** (most feature-complete)
- **PyPI**: `sqlalchemy-vectorstores[sqlite]` 
- **Description**: A vectorstore supporting both vector and BM25 search using SQLite or PostgreSQL through SQLAlchemy .

**Key Features:**
- Full document CRUD operations (create, read, update, delete)
- BM25 search with SQLite FTS
- Supports custom tokenizers (including Chinese with jieba)
- Metadata filtering during search
- Synchronous and async APIs
- Built on SQLAlchemy, so unique constraints are enforced at the database level

**Example:**
```python
from sqlalchemy_vectorstores import SqliteDatabase, SqliteVectorStore

db = SqliteDatabase("sqlite:///search.db", echo=False)
vs = SqliteVectorStore(db, dim=384, fts_tokenize="simple")

# Add source (document)
src_id = vs.add_source(url="file.pdf", tags=["a", "b"])

# Add document with content
vs.add_document(src_id=src_id, content="Your text here")

# Search by BM25
results = vs.search_by_bm25("query")

# Delete source (cascades to documents)
vs.delete_source(src_id)
```

**Limitations**: More complex setup as it's designed for both vector and BM25 search .

---

## Other Options

### 3. **Haystack's InMemoryBM25Retriever**
- **Library**: `haystack` (full RAG framework)
- **Description**: BM25 retriever that works with `InMemoryDocumentStore` .

**Key Features:**
- BM25 algorithm implementation
- Filtering support
- Good integration if you're building RAG pipelines

**Limitations**: 
- **No SQLite persistence**—in-memory only 
- Does not meet your "load from disk" requirement

---

### 4. **bm25s** (pure Python BM25)
- **PyPI**: `bm25s` 
- **Description**: A lightweight BM25 library with high-level API and optional CLI .

**Key Features:**
- Pure NumPy implementation (no scipy dependency)
- High-level 1-line indexing API
- Command-line interface
- MCP server support for LLM integration

**Limitations**:
- Not SQLite-backed by default (uses file-based storage)
- Your custom implementation may be more suitable for SQLite persistence

---

### 5. **python3-sqlite-fts4**
- **Debian package**: Provides SQLite functions for BM25 scoring 
- **Description**: Custom SQLite functions written in Python for ranking documents indexed with SQLite FTS4 .

**Limitations**:
- This is a low-level function package, not a complete indexing library
- You'd still need to build the document management layer yourself

---

## Comparison Table

| Library | SQLite Persistence | Unique ID Constraints | Existence Check | Document CRUD |
|---------|-------------------|----------------------|-----------------|---------------|
| **sqlitesearch** | ✅ Yes | ✅ (id_field) | ⚠️ Implicit | ✅ add/clear |
| **sqlalchemy-vectorstores** | ✅ Yes | ✅ (SQLAlchemy) | ✅ Direct | ✅ Full CRUD |
| **Haystack** | ❌ No (in-memory) | ✅ | ⚠️ Implicit | ✅ add/delete |
| **bm25s** | ❌ No | ✅ | ⚠️ Via ID lookup | ✅ Via API |
| **Your custom implementation** | ✅ Yes | ✅ | ✅ Explicit | ✅ Full |

---

## Recommendation

If you want a drop-in replacement that closely matches your custom implementation, **`sqlitesearch`** is your best bet—it's purpose-built for SQLite-backed BM25 search and has a clean, minimal API .

If you anticipate needing more features (metadata filtering, vector search, async support), **`sqlalchemy-vectorstores`** provides a more robust foundation with full CRUD operations .

If you'd prefer to stick with your own implementation, that's also perfectly valid—you have full control over the schema and behavior, and the library you built already meets all your stated requirements.