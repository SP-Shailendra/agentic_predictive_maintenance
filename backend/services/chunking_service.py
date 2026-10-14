from dataclasses import dataclass

from services.document_loader import DocumentSegment


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    text: str
    chunk_index: int
    page_number: int | None
    section_title: str | None


def chunk_segments(
    segments: list[DocumentSegment],
    *,
    max_words: int = 500,
    overlap_words: int = 75,
) -> list[DocumentChunk]:
    if max_words < 1 or overlap_words < 0 or overlap_words >= max_words:
        raise ValueError("Chunk size must be positive and overlap must be smaller than chunk size.")

    groups: list[tuple[int | None, str | None, list[str]]] = []
    for segment in segments:
        if groups and groups[-1][:2] == (segment.page_number, segment.section_title):
            groups[-1][2].extend(segment.text.split())
        else:
            groups.append((segment.page_number, segment.section_title, segment.text.split()))

    chunks: list[DocumentChunk] = []
    for page_number, section_title, words in groups:
        start = 0
        while start < len(words):
            end = min(start + max_words, len(words))
            chunks.append(DocumentChunk(
                text=" ".join(words[start:end]),
                chunk_index=len(chunks),
                page_number=page_number,
                section_title=section_title,
            ))
            if end == len(words):
                break
            start = end - overlap_words
    return chunks