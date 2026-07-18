# Semantic-search roadmap

Semantic search will be an optional extra. Embedding providers and vector indexes
will implement domain protocols, cache by model and content hash, and never be
required for lexical operations. Hybrid ranking will combine lexical, structural,
path, and semantic candidates without using an LLM to classify the query.
