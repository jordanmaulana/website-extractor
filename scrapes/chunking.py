"""Markdown-aware chunker for RAG indexing.

Splits markdown into token-bounded chunks while respecting block boundaries:
- Keeps a heading with the first block under it
- Treats fenced code as atomic during packing
- Falls back to sentence then word split for blocks that exceed the budget
- Prepends CHUNK_OVERLAP_TOKENS worth of tokens from the previous chunk
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import tiktoken
from django.conf import settings
from markdown_it import MarkdownIt


_ENCODER: tiktoken.Encoding | None = None


def _encoder() -> tiktoken.Encoding:
    global _ENCODER
    if _ENCODER is None:
        _ENCODER = tiktoken.encoding_for_model(settings.RAG["EMBEDDING_MODEL"])
    return _ENCODER


def _token_count(s: str) -> int:
    return len(_encoder().encode(s))


@dataclass
class ChunkSpec:
    text: str
    token_count: int
    heading_path: list[str] = field(default_factory=list)


@dataclass
class _Block:
    text: str
    kind: str  # 'heading', 'paragraph', 'fence', 'bullet_list', 'blockquote', ...
    heading_level: int = 0  # h1..h6 (0 for non-heading)
    heading_text: str = ""
    token_count: int = 0


def _extract_blocks(md: str) -> list[_Block]:
    """Walk the markdown-it token stream and produce top-level source blocks."""
    parser = MarkdownIt("commonmark")
    tokens = parser.parse(md)
    lines = md.splitlines()
    blocks: list[_Block] = []

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.level != 0:
            i += 1
            continue

        if tok.nesting == 1:
            start, end = tok.map if tok.map else (None, None)
            text = "\n".join(lines[start:end]).strip() if start is not None else ""

            kind = tok.type.removesuffix("_open")
            heading_level = 0
            heading_text = ""
            if kind == "heading":
                heading_level = int(tok.tag[1:])
                for j in range(i + 1, len(tokens)):
                    if tokens[j].type == "inline":
                        heading_text = tokens[j].content
                        break

            if text:
                blocks.append(
                    _Block(
                        text=text,
                        kind=kind,
                        heading_level=heading_level,
                        heading_text=heading_text,
                        token_count=_token_count(text),
                    )
                )

            # Skip to the matching close at level 0.
            i += 1
            while i < len(tokens):
                if tokens[i].level == 0 and tokens[i].nesting == -1:
                    i += 1
                    break
                i += 1
            continue

        if tok.nesting == 0:
            start, end = tok.map if tok.map else (None, None)
            if start is not None and end is not None:
                text = "\n".join(lines[start:end]).strip()
            else:
                text = tok.content.strip()
            if text:
                blocks.append(
                    _Block(text=text, kind=tok.type, token_count=_token_count(text))
                )
            i += 1
            continue

        i += 1

    return blocks


def _fallback_split(block: _Block, budget: int) -> list[_Block]:
    """Break a single oversize block into sentence- then word-bounded pieces."""

    def flush(buf: list[str]) -> _Block | None:
        text = " ".join(buf).strip()
        if not text:
            return None
        return _Block(text=text, kind=block.kind, token_count=_token_count(text))

    pieces: list[_Block] = []
    sentences = re.split(r"(?<=[.!?])\s+", block.text)

    buf: list[str] = []
    buf_tokens = 0
    for sentence in sentences:
        if not sentence.strip():
            continue
        sent_tokens = _token_count(sentence)

        if sent_tokens > budget:
            emitted = flush(buf)
            if emitted:
                pieces.append(emitted)
            buf, buf_tokens = [], 0

            word_buf: list[str] = []
            word_tokens = 0
            for word in sentence.split():
                wt = _token_count(word + " ") or 1
                if word_tokens + wt > budget and word_buf:
                    emitted = flush(word_buf)
                    if emitted:
                        pieces.append(emitted)
                    word_buf = [word]
                    word_tokens = wt
                else:
                    word_buf.append(word)
                    word_tokens += wt
            emitted = flush(word_buf)
            if emitted:
                pieces.append(emitted)
            continue

        if buf_tokens + sent_tokens > budget and buf:
            emitted = flush(buf)
            if emitted:
                pieces.append(emitted)
            buf = [sentence]
            buf_tokens = sent_tokens
        else:
            buf.append(sentence)
            buf_tokens += sent_tokens

    emitted = flush(buf)
    if emitted:
        pieces.append(emitted)
    return pieces


def chunk_markdown(
    md: str,
    *,
    budget: int | None = None,
    overlap: int | None = None,
) -> list[ChunkSpec]:
    """Split markdown into ChunkSpec pieces.

    ``budget`` and ``overlap`` override ``settings.RAG["CHUNK_TOKENS"]`` /
    ``CHUNK_OVERLAP_TOKENS`` for tests.
    """
    if not md or not md.strip():
        return []

    budget = budget if budget is not None else settings.RAG["CHUNK_TOKENS"]
    overlap = overlap if overlap is not None else settings.RAG["CHUNK_OVERLAP_TOKENS"]

    blocks = _extract_blocks(md)
    if not blocks:
        return []

    # Expand any block exceeding the budget into atomic pieces.
    atoms: list[_Block] = []
    for block in blocks:
        if block.token_count > budget:
            atoms.extend(_fallback_split(block, budget))
        else:
            atoms.append(block)

    enc = _encoder()
    chunks: list[ChunkSpec] = []
    heading_stack: list[tuple[int, str]] = []
    buf: list[_Block] = []
    buf_tokens = 0
    buf_heading_path: list[str] = []

    def flush() -> None:
        nonlocal buf, buf_tokens
        if not buf:
            return
        body = "\n\n".join(b.text for b in buf).strip()
        if not body:
            buf, buf_tokens = [], 0
            return
        text = body
        if chunks and overlap > 0:
            prev_tokens = enc.encode(chunks[-1].text)
            tail = prev_tokens[-overlap:]
            if tail:
                text = enc.decode(tail) + "\n\n" + body
        chunks.append(
            ChunkSpec(
                text=text,
                token_count=len(enc.encode(text)),
                heading_path=buf_heading_path.copy(),
            )
        )
        buf, buf_tokens = [], 0

    i = 0
    while i < len(atoms):
        atom = atoms[i]

        if atom.kind == "heading":
            while heading_stack and heading_stack[-1][0] >= atom.heading_level:
                heading_stack.pop()
            heading_stack.append((atom.heading_level, atom.heading_text))
            new_path = [t for _, t in heading_stack]

            # A new heading arriving after body text ends the previous section.
            if any(b.kind != "heading" for b in buf):
                flush()

            next_atom = atoms[i + 1] if i + 1 < len(atoms) else None
            pair_tokens = atom.token_count + (
                next_atom.token_count
                if next_atom and next_atom.kind != "heading"
                else 0
            )
            if buf and buf_tokens + pair_tokens > budget:
                flush()

            if not buf:
                buf_heading_path = new_path

            buf.append(atom)
            buf_tokens += atom.token_count
            i += 1
            continue

        if buf and buf_tokens + atom.token_count > budget:
            flush()
        if not buf:
            buf_heading_path = [t for _, t in heading_stack]

        buf.append(atom)
        buf_tokens += atom.token_count
        i += 1

    flush()
    return chunks
