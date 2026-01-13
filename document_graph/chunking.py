from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterator, Optional

from document_graph.config import ChunkingConfig
from document_graph.document_parsing import read_for_chunking


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Chunk:
    title_path: list[str]
    offset_start: int
    offset_end: int
    text: str
    chunk_type: str = "text"


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self._chunks.append(text)

    def text(self) -> str:
        return "\n".join(self._chunks)


def _normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_html_text(html: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(html)
    return _normalize_whitespace(parser.text())


def _iter_md_sections(md: str) -> Iterator[tuple[list[str], str]]:
    lines = md.splitlines()
    title_path: list[str] = []
    buf: list[str] = []
    current_level: Optional[int] = None

    def flush() -> Optional[tuple[list[str], str]]:
        nonlocal buf
        if not buf:
            return None
        content = _normalize_whitespace("\n".join(buf))
        buf = []
        return (title_path.copy(), content) if content else None

    for line in lines:
        m = re.match(r"^(#{1,6})\s+(.*)\s*$", line)
        if m:
            out = flush()
            if out:
                yield out
            level = len(m.group(1))
            title = m.group(2).strip()
            if current_level is None:
                title_path = [title]
            else:
                title_path = title_path[: max(level - 1, 0)]
                title_path.append(title)
            current_level = level
            continue
        buf.append(line)

    out = flush()
    if out:
        yield out


def _iter_plain_sections(text: str) -> Iterator[tuple[list[str], str]]:
    cleaned = _normalize_whitespace(text)
    if cleaned:
        yield ([], cleaned)


def _split_paragraphs(text: str) -> list[str]:
    parts = [p.strip() for p in text.split("\n\n")]
    return [p for p in parts if p]


def _sliding_chunks(
    text: str,
    *,
    target_chars: int,
    max_chars: int,
    overlap_chars: int,
) -> Iterator[tuple[int, int, str]]:
    text = text.strip()
    if not text:
        return
    if len(text) <= max_chars:
        yield (0, len(text), text)
        return

    start = 0
    n = len(text)
    while start < n:
        end = min(start + target_chars, n)
        hard_end = min(start + max_chars, n)

        candidate = text.rfind("\n\n", start, hard_end)
        if candidate != -1 and candidate > start + int(target_chars * 0.6):
            end = candidate
        else:
            end = hard_end

        chunk_text = text[start:end].strip()
        if chunk_text:
            yield (start, end, chunk_text)

        if end >= n:
            break
        start = max(0, end - overlap_chars)


def _iter_chunks_for_section(
    section_text: str,
    *,
    target_chars: int,
    max_chars: int,
    overlap_chars: int,
) -> Iterator[tuple[int, int, str, str]]:
    paragraphs = _split_paragraphs(section_text)
    if not paragraphs:
        return

    if len(section_text) <= max_chars:
        yield (0, len(section_text), section_text, "text")
        return

    buf: list[str] = []
    buf_len = 0
    offset = 0
    produced: list[tuple[int, int, str, str]] = []
    for p in paragraphs:
        p_len = len(p) + 2
        if buf and buf_len + p_len > target_chars:
            chunk_text = "\n\n".join(buf).strip()
            if chunk_text:
                produced.append((offset, offset + len(chunk_text), chunk_text, "text"))
            offset = max(0, offset + len(chunk_text) - overlap_chars)
            buf = []
            buf_len = 0
        buf.append(p)
        buf_len += p_len

    tail = "\n\n".join(buf).strip()
    if tail:
        produced.append((offset, offset + len(tail), tail, "text"))

    if any(len(c[2]) > max_chars for c in produced):
        for start, end, txt in _sliding_chunks(
            section_text,
            target_chars=target_chars,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        ):
            yield (start, end, txt, "text")
        return

    for c in produced:
        yield c


def iter_chunks_for_file(path: Path, *, chunking: ChunkingConfig) -> Iterator[Chunk]:
    raw, kind = read_for_chunking(path)
    logger.info("chunking_start path=%s kind=%s", path, kind)
    if kind in {"html", "htm"}:
        sections = _iter_plain_sections(_extract_html_text(raw))
    elif kind == "md":
        sections = _iter_md_sections(raw)
    else:
        sections = _iter_plain_sections(raw)

    offset_base = 0
    for title_path, section_text in sections:
        for start, end, chunk_text, chunk_type in _iter_chunks_for_section(
            section_text,
            target_chars=chunking.target_chars,
            max_chars=chunking.max_chars,
            overlap_chars=chunking.overlap_chars,
        ):
            yield Chunk(
                title_path=title_path,
                offset_start=offset_base + start,
                offset_end=offset_base + end,
                text=chunk_text,
                chunk_type=chunk_type,
            )
        offset_base += len(section_text) + 2
    logger.info("chunking_done path=%s", path)

