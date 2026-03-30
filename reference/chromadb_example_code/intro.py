# for pure vector search, use duckdb instead. offer document id constraint?
# https://developers.llamaindex.ai/python/framework-api-reference/storage/vector_store/duckdb/

# https://blog.brunk.io/posts/similarity-search-with-duckdb
# https://duckdb.org/docs/lts/core_extensions/vss

# check out more embedding search backend in llamaindex documentation
# https://developers.llamaindex.ai/python/framework-api-reference/storage/docstore/

# Chroma could be overkill. Let's use some pure vector search engine instead? it uses onnxruntime for sentence transformer. maybe we should also use onnxruntime for whisper or image embedding models? is there anything like whisper-rs? checkout deepwiki for screenpipe (https://deepwiki.com/screenpipe/screenpipe/2.3-audio-processing)?

# example repo for whisper, image embedding and more (transformer.js): https://github.com/chroma-core/chromadb-default-embed

# example usage: https://realpython.com/chromadb-vector-database/

import chromadb
# setup Chroma in-memory, for easy prototyping. Can add persistence easily!
client = chromadb.Client()

# Create collection. get_collection, get_or_create_collection, delete_collection also available!
collection = client.create_collection("all-my-documents")

# Add docs to the collection. Can also update and delete. Row-based API coming soon!
# Notice, you can just pass embeddings to collection.add, without documents. you may pass document id to metadatas, or "screenshot_path" if your embedding is generated from photo
# You must first record embedding dimension elsewhere, and throw embedding dimension mismatch exception without chroma does it for you.
collection.add(
    documents=["This is document1", "This is document2"], # we handle tokenization, embedding, and indexing automatically. You can skip that and add your own embeddings as well
    embeddings=[[1.1, 2.3, 3.2], [4.5, 6.9, 4.4], [1.1, 2.3, 3.2]],
    metadatas=[{"source": "notion"}, {"source": "google-docs"}], # filter on these!
    ids=["doc1", "doc2"], # unique for each doc
)

# Query/search 2 most similar results. You can also .get by id
results = collection.query(
    query_texts=["This is a query document"],
    n_results=2,
    # where={"metadata_field": "is_equal_to_this"}, # optional filter
    # where_document={"$contains":"search_string"}  # optional filter
)