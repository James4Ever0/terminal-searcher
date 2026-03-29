# The IR book recommends default values of k1 between 1.2 and 2.0, and b=0.75
retriever = bm25s.BM25(method="robertson", k1=1.5, b=0.75)

# For BM25+, BM25L, you need a delta parameter (default is 0.5)
retriever = bm25s.BM25(method="bm25+", delta=1.5)

# You can also choose a different "method" for idf, while keeping the default for the rest
# for example, this is equivalent to rank-bm25 when `epsilon=0`
retriever = bm25s.BM25(method="atire", idf_method="robertson")
# and this is equivalent to bm25-pt
retriever = bm25s.BM25(method="atire", idf_method="lucene")