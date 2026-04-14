# Knowledge package
#
# Handles ingestion, processing, and retrieval of ARUP Laboratories knowledge documents.
#
#   - processor.py : parses raw files (JSON, PDF, CSV) into text chunks with rich metadata,
#                    classifying each document as Algorithm, Fact Sheet, Consult Topic,
#                    Test Directory entry, or General content.
#   - store.py     : wraps a persistent ChromaDB collection, providing add, search (with
#                    optional metadata filtering), deduplication, and algorithm-graph lookup.
