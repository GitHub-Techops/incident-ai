"""Inspect the knowledge base as the RAG pipeline sees it.

    python -m ingestion                         # every document and its chunks
    python -m ingestion --show runbooks/database-errors.md
    python -m ingestion --grep "connection refused"

--grep is a plain keyword search: a sanity check until semantic search
(embeddings + Chroma) arrives in Milestone 12.
"""

import argparse
from pathlib import Path

from ingestion.chunking import MAX_CHARS, chunk_document
from ingestion.documents import DEFAULT_KNOWLEDGE_DIR, load_documents


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m ingestion", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--knowledge-dir", type=Path, default=DEFAULT_KNOWLEDGE_DIR)
    parser.add_argument("--max-chars", type=int, default=MAX_CHARS)
    parser.add_argument("--show", metavar="SOURCE", help="print every chunk of one document")
    parser.add_argument("--grep", metavar="TEXT", help="print the chunks containing TEXT (case-insensitive)")
    args = parser.parse_args()

    docs = load_documents(args.knowledge_dir)
    chunks = {doc.source: chunk_document(doc, args.max_chars) for doc in docs}

    if args.show:
        for chunk in chunks[args.show]:
            print(f"--- {chunk.chunk_id}  ({len(chunk.text)} chars)\n{chunk.text}\n")
        return

    if args.grep:
        needle = args.grep.lower()
        hits = [c for doc_chunks in chunks.values() for c in doc_chunks if needle in c.text.lower()]
        for chunk in hits:
            print(f"{chunk.chunk_id:<62} {chunk.section or '(intro)'}")
        print(f"\n{len(hits)} chunks contain {args.grep!r}")
        return

    print(f"{'source':<55} {'type':<16} {'chunks':>6} {'max chars':>9}")
    for doc in docs:
        doc_chunks = chunks[doc.source]
        print(f"{doc.source:<55} {doc.type:<16} {len(doc_chunks):>6} "
              f"{max(len(c.text) for c in doc_chunks):>9}")
    total = sum(len(c) for c in chunks.values())
    print(f"\n{len(docs)} documents, {total} chunks (max {args.max_chars} chars each)")


if __name__ == "__main__":
    main()
