from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePath
import re
from zipfile import BadZipFile, ZipFile

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader


MAX_DOCX_EXPANDED_BYTES = 50 * 1024 * 1024
MAX_DOCX_ARCHIVE_ENTRIES = 5000
MAX_EXTRACTED_TEXT_CHARS = 5_000_000


class DocumentLoadError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class DocumentSegment:
    text: str
    page_number: int | None = None
    section_title: str | None = None


def _normalized_segment(text: str, page_number: int | None = None, section_title: str | None = None) -> DocumentSegment | None:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return None
    return DocumentSegment(normalized, page_number, section_title)


def _load_text(raw: bytes, markdown: bool) -> list[DocumentSegment]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise DocumentLoadError("invalid_encoding", "Text documents must use UTF-8 encoding.") from error
    segments: list[DocumentSegment] = []
    section_title = None
    for line in text.splitlines():
        stripped = line.strip()
        if markdown and stripped.startswith("#"):
            section_title = stripped.lstrip("#").strip() or section_title
            continue
        segment = _normalized_segment(stripped, section_title=section_title)
        if segment:
            segments.append(segment)
    return segments


def _load_docx(raw: bytes) -> list[DocumentSegment]:
    try:
        with ZipFile(BytesIO(raw)) as archive:
            entries = archive.infolist()
            expanded_bytes = sum(entry.file_size for entry in entries)
            if len(entries) > MAX_DOCX_ARCHIVE_ENTRIES or expanded_bytes > MAX_DOCX_EXPANDED_BYTES:
                raise DocumentLoadError("document_too_complex", "DOCX archive exceeds the safe extraction limit.")
            if any(entry.file_size > 0 and entry.compress_size == 0 for entry in entries):
                raise DocumentLoadError("invalid_docx", "DOCX archive contains an invalid compressed entry.")
            if any(entry.compress_size and entry.file_size / entry.compress_size > 200 for entry in entries):
                raise DocumentLoadError("document_too_complex", "DOCX compression ratio exceeds the safe extraction limit.")
            if "[Content_Types].xml" not in archive.namelist():
                raise DocumentLoadError("invalid_docx", "Uploaded file is not a valid DOCX document.")
        document = Document(BytesIO(raw))
    except DocumentLoadError:
        raise
    except (BadZipFile, KeyError, ValueError) as error:
        raise DocumentLoadError("invalid_docx", "Uploaded file is not a valid DOCX document.") from error

    segments: list[DocumentSegment] = []
    section_title = None
    for element in document.iter_inner_content():
        if isinstance(element, Paragraph):
            if element.style and element.style.name.startswith("Heading") and element.text.strip():
                section_title = element.text.strip()
            segment = _normalized_segment(element.text, section_title=section_title)
            if segment:
                segments.append(segment)
        elif isinstance(element, Table):
            for row in element.rows:
                row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                segment = _normalized_segment(row_text, section_title=section_title)
                if segment:
                    segments.append(segment)
    return segments


def _load_pdf(raw: bytes) -> list[DocumentSegment]:
    try:
        reader = PdfReader(BytesIO(raw), strict=True)
        if reader.is_encrypted:
            raise DocumentLoadError("encrypted_pdf", "Encrypted PDFs are not supported.")
        segments = []
        for page_number, page in enumerate(reader.pages, start=1):
            segment = _normalized_segment(page.extract_text() or "", page_number=page_number)
            if segment:
                segments.append(segment)
        return segments
    except DocumentLoadError:
        raise
    except Exception as error:
        raise DocumentLoadError("invalid_pdf", "Uploaded file could not be parsed as a PDF.") from error


def load_document(filename: str, raw: bytes) -> list[DocumentSegment]:
    extension = PurePath(filename).suffix.lower()
    if extension in {".txt", ".md", ".markdown"}:
        segments = _load_text(raw, markdown=extension != ".txt")
    elif extension == ".docx":
        segments = _load_docx(raw)
    elif extension == ".pdf":
        segments = _load_pdf(raw)
    else:
        raise DocumentLoadError("unsupported_format", "Supported formats are PDF, DOCX, TXT, and Markdown.")
    if sum(len(segment.text) for segment in segments) > MAX_EXTRACTED_TEXT_CHARS:
        raise DocumentLoadError("extracted_text_too_large", "Extracted document text exceeds the safe processing limit.")
    if not segments:
        raise DocumentLoadError("no_extractable_text", "No extractable text was found in the document.")
    return segments