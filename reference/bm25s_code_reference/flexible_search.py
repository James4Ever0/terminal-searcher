# You can provide a list of queries instead of a single query
queries = ["What is a cat?", "is the bird a dog?"]

# Provide your own stopwords list if you don't like the default one
stopwords = ["a", "the"]

# For stemming, use any function that is callable on each word list
stemmer_fn = lambda lst: [word for word in lst]

# Tokenize the queries
query_token_ids = bm25s.tokenize(queries, stopwords=stopwords, stemmer=stemmer_fn)

# If you want the tokenizer to return strings instead of token ids, you can do this
query_token_strs = bm25s.tokenize(queries, return_ids=False)

# You can use a different corpus for retrieval, e.g., titles instead of full docs
titles = ["About Cat", "About Dog", "About Bird", "About Fish"]

# You can also choose to only return the documents and omit the scores
# note: if you pass a new corpus here, it must have the same length as your indexed corpus
results = retriever.retrieve(query_token_ids, corpus=titles, k=2, return_as="documents")

# The documents are returned as a numpy array of shape (n_queries, k)
for i in range(results.shape[1]):
    print(f"Rank {i+1}: {results[0, i]}")