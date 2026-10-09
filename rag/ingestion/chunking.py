"""Split documents into chunks: the units that get embedded and retrieved.

Why split by section: a runbook section ("Check recent changes") answers one
question. A chunk holding one section matches a question about it well; a chunk
holding a whole document matches everything a little. And the LLM's prompt
only has room for a few chunks, so each one should be focused.

Rules:
  - `##` and `###` headings start a new section; `#` is the document title.
  - Each chunk starts with "<title> > <section>" so it still makes sense on its
    own (a chunk "Roll back" is ambiguous; "Bad deployment and rollback > Roll
    back" is not).
  - A section longer than max_chars is split on blank lines, never inside a
    fenced code block.
"""

import hashlib
import re
from dataclasses import dataclass

from ingestion.documents import Document

MAX_CHARS = 1500
HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
FENCE = "```"


@dataclass(frozen=True)
class Chunk:
    chunk_id: str  # "<source>#<index>", stable as long as the document doesn't change
    source: str  # the document's path relative to knowledge/
    title: str
    doc_type: str
    section: str  # heading path, e.g. "Check the client configuration"; "" for the intro
    index: int  # position in the document
    text: str  # what gets embedded and shown: context header + section content
    content_hash: str  # detects changed chunks when re-ingesting
    services: tuple[str, ...] = ()
    alerts: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


def split_sections(body: str) -> list[tuple[str, str]]:
    """(heading path, content) for each section, in order. Headings inside code blocks are content."""
    sections: list[tuple[str, str]] = []
    path: list[str] = []
    lines: list[str] = []
    in_fence = False

    def flush() -> None:
        content = "\n".join(lines).strip()
        if content:
            sections.append((" > ".join(path), content))
        lines.clear()

    for line in body.splitlines():
        if line.strip().startswith(FENCE):
            in_fence = not in_fence
        match = None if in_fence else HEADING.match(line)
        if match:
            flush()
            level, heading = len(match.group(1)), match.group(2)
            path = [] if level == 1 else [heading] if level == 2 else path[:1] + [heading]
            continue
        lines.append(line)
    flush()
    return sections


def _blocks(content: str) -> list[str]:
    """Paragraphs separated by blank lines; a fenced code block is always one block."""
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in content.splitlines():
        if line.strip().startswith(FENCE):
            in_fence = not in_fence
        if not line.strip() and not in_fence:
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _hard_split(block: str, budget: int) -> list[str]:
    """Last resort for one block longer than the budget: split on lines, then characters."""
    pieces, current = [], ""
    for line in block.splitlines():
        while len(line) > budget:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:budget])
            line = line[budget:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > budget:
            pieces.append(current)
            current = line
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def _pack(content: str, budget: int) -> list[str]:
    """Greedily group blocks into pieces of at most `budget` characters."""
    pieces: list[str] = []
    current = ""
    for block in _blocks(content):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= budget:
            current = candidate
            continue
        if current:
            pieces.append(current)
        if len(block) <= budget:
            current = block
        else:
            *full, current = _hard_split(block, budget)
            pieces.extend(full)
    if current:
        pieces.append(current)
    return pieces


def chunk_document(doc: Document, max_chars: int = MAX_CHARS) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section, content in split_sections(doc.body):
        header = f"{doc.title} > {section}" if section else doc.title
        budget = max_chars - len(header) - 2  # "\n\n" between header and content
        if budget < 200:
            raise ValueError(f"{doc.source}: heading too long for max_chars={max_chars}: {header!r}")
        for piece in _pack(content, budget):
            text = f"{header}\n\n{piece}"
            index = len(chunks)
            chunks.append(Chunk(
                chunk_id=f"{doc.source}#{index}", source=doc.source, title=doc.title,
                doc_type=doc.type, section=section, index=index, text=text,
                content_hash=hashlib.sha256(text.encode()).hexdigest()[:16],
                services=doc.services, alerts=doc.alerts, tags=doc.tags,
            ))
    return chunks


def chunk_documents(docs: list[Document], max_chars: int = MAX_CHARS) -> list[Chunk]:
    return [chunk for doc in docs for chunk in chunk_document(doc, max_chars)]
