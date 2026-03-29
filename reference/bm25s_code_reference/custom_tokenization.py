from bm25s.tokenization import Tokenizer

corpus = [
      "a cat is a feline and likes to purr",
      "a dog is the human's best friend and loves to play",
      "a bird is a beautiful animal that can fly",
      "a fish is a creature that lives in water and swims",
]

# Pick your favorite stemmer, and pass 
stemmer = None
stopwords = ["is"]
splitter = lambda x: x.split() # function or regex pattern
# Create a tokenizer
tokenizer = Tokenizer(
      stemmer=stemmer, stopwords=stopwords, splitter=splitter
)

corpus_tokens = tokenizer.tokenize(corpus)

# let's see what the tokens look like
print("tokens:", corpus_tokens)
print("vocab:", tokenizer.get_vocab_dict())

# note: the vocab dict will either be a dict of `word -> id` if you don't have a stemmer, and a dict of `stemmed word -> stem id` if you do.
# You can save the vocab. it's fine to use the same dir as your index if filename doesn't conflict
tokenizer.save_vocab(save_dir="bm25s_very_big_index")

# loading:
new_tokenizer = Tokenizer(stemmer=stemmer, stopwords=[], splitter=splitter)
new_tokenizer.load_vocab("bm25s_very_big_index")
print("vocab reloaded:", new_tokenizer.get_vocab_dict())

# the same can be done for stopwords
print("stopwords before reload:", new_tokenizer.stopwords)
tokenizer.save_stopwords(save_dir="bm25s_very_big_index")
new_tokenizer.load_stopwords("bm25s_very_big_index")
print("stopwords reloaded:", new_tokenizer.stopwords)