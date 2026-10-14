"""Index the knowledge base into Redis and search it.

    python -m retrieval index                 # embed new/changed documents
    python -m retrieval index --force         # re-embed everything
    python -m retrieval search "How do we troubleshoot database connection failures?"
    python -m retrieval search "pods keep restarting" -k 5 --type runbook

Needs Redis (docker compose -f compose.yaml up -d) and Ollama with nomic-embed-text.
Settings: REDIS_URL (default redis://localhost:6379/0), OLLAMA_URL
(default http://localhost:11434), EMBED_MODEL (default nomic-embed-text).

From WSL, Ollama runs on the Windows host, whose address changes on reboot:
    export OLLAMA_URL="http://$(ip route show default | awk '{print $3}'):11434"
"""

import argparse
import os
import sys

import redis

from embeddings.ollama import EmbeddingError, OllamaEmbedder
from ingestion.documents import load_documents
from retrieval.indexer import index_documents
from retrieval.redis_store import RedisKnowledgeStore

EMBED_DIM = 768  # nomic-embed-text


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m retrieval", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    index_cmd = sub.add_parser("index", help="embed and store the knowledge base")
    index_cmd.add_argument("--force", action="store_true", help="re-embed unchanged documents too")
    search_cmd = sub.add_parser("search", help="find the documents closest in meaning to a question")
    search_cmd.add_argument("question")
    search_cmd.add_argument("-k", type=int, default=3, help="number of results (default 3)")
    search_cmd.add_argument("--type", choices=["runbook", "troubleshooting", "architecture", "incident"])
    search_cmd.add_argument("--service", help="only documents for this service (plus generic ones)")
    args = parser.parse_args()

    client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
    embedder = OllamaEmbedder(os.getenv("OLLAMA_URL", "http://localhost:11434"),
                              os.getenv("EMBED_MODEL", "nomic-embed-text"))
    store = RedisKnowledgeStore(client, dim=EMBED_DIM)

    try:
        if args.command == "index":
            report = index_documents(load_documents(), embedder, store, force=args.force)
            for label in ("added", "updated", "unchanged", "removed"):
                print(f"{label:<10} {len(getattr(report, label)):>3}  {', '.join(getattr(report, label))}")
            return

        store.ensure_index()
        results = store.search(embedder.embed_query(args.question), k=args.k,
                               doc_type=args.type, service=args.service)
        print(f"Q: {args.question}\n")
        for rank, hit in enumerate(results, 1):
            print(f"{rank}. {hit.relevance:.3f}  {hit.source:<55} {hit.title}")
        if not results:
            print("no results (run `python -m retrieval index` first?)")
    except EmbeddingError as exc:
        sys.exit(f"embedding failed: {exc}")
    except redis.ConnectionError as exc:
        sys.exit(f"cannot reach Redis: {exc} (docker compose -f compose.yaml up -d)")


if __name__ == "__main__":
    main()
