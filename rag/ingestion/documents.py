"""Load the knowledge base: Markdown files with a YAML front matter block.

Each document keeps its path relative to knowledge/ as `source`, so every
search result can say exactly which file it came from.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Folder -> document type. A document's `type` must match its folder.
DOC_TYPES = {
    "runbooks": "runbook",
    "troubleshooting": "troubleshooting",
    "architecture": "architecture",
    "incidents": "incident",
}
DEFAULT_KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "knowledge"
LIST_FIELDS = ("services", "alerts", "tags")


class DocumentError(ValueError):
    """A knowledge document is malformed. The message names the file."""


@dataclass(frozen=True)
class Document:
    source: str  # relative to knowledge/, e.g. "runbooks/high-error-rate.md"
    title: str
    type: str  # runbook | troubleshooting | architecture | incident
    body: str  # Markdown without the front matter
    services: tuple[str, ...] = ()
    alerts: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    extra: dict[str, str] = field(default_factory=dict)  # other front matter, e.g. date, severity


def _split_front_matter(text: str, source: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise DocumentError(f"{source}: must start with a '---' front matter block")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise DocumentError(f"{source}: front matter block is not closed with '---'")
    try:
        meta = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as exc:
        raise DocumentError(f"{source}: invalid front matter: {exc}") from exc
    if not isinstance(meta, dict):
        raise DocumentError(f"{source}: front matter must be a mapping")
    return meta, text[end + len("\n---\n"):]


def parse_document(text: str, source: str) -> Document:
    meta, body = _split_front_matter(text, source)

    title = meta.pop("title", None)
    if not isinstance(title, str) or not title.strip():
        raise DocumentError(f"{source}: 'title' is required")
    doc_type = meta.pop("type", None)
    if doc_type not in DOC_TYPES.values():
        raise DocumentError(f"{source}: 'type' must be one of {sorted(DOC_TYPES.values())}, got {doc_type!r}")

    lists: dict[str, tuple[str, ...]] = {}
    for name in LIST_FIELDS:
        value = meta.pop(name, None) or []
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise DocumentError(f"{source}: '{name}' must be a list of strings")
        lists[name] = tuple(value)

    return Document(
        source=source, title=title.strip(), type=doc_type, body=body.strip("\n") + "\n",
        extra={key: str(value) for key, value in meta.items()}, **lists,
    )


def load_documents(root: Path = DEFAULT_KNOWLEDGE_DIR) -> list[Document]:
    """Every Markdown document under root, sorted by path. README.md files are skipped."""
    root = Path(root)
    documents = []
    for path in sorted(root.rglob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        source = path.relative_to(root).as_posix()
        folder = source.split("/")[0]
        if folder not in DOC_TYPES:
            raise DocumentError(f"{source}: must be in one of the folders {sorted(DOC_TYPES)}")
        doc = parse_document(path.read_text(encoding="utf-8"), source)
        if doc.type != DOC_TYPES[folder]:
            raise DocumentError(f"{source}: type {doc.type!r} does not match folder {folder!r}")
        documents.append(doc)
    return documents
