"""Parse supported source documents into plain text."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from bankwise.rag.errors import RagError
from bankwise.rag.models import Document


class DocumentParser:
    """Read UTF-8 text/Markdown and, when installed, PDF documents."""

    def __init__(self, max_file_bytes: int = 20 * 1024 * 1024) -> None:
        if isinstance(max_file_bytes, bool) or not isinstance(max_file_bytes, int) or max_file_bytes < 1:
            raise RagError("max_file_bytes must be a positive integer")
        self.max_file_bytes = max_file_bytes

    def parse_file(self, path: Path) -> Document:
        source_path = Path(path)
        if not source_path.is_file():
            raise RagError(f"document does not exist or is not a file: {source_path}")
        size = source_path.stat().st_size
        if size > self.max_file_bytes:
            raise RagError(f"document exceeds the {self.max_file_bytes}-byte limit")

        suffix = source_path.suffix.lower()
        if suffix in (".txt", ".md", ".markdown"):
            text = source_path.read_text(encoding="utf-8-sig")
        elif suffix == ".pdf":
            text = self._parse_pdf(source_path)
        else:
            raise RagError(f"unsupported document type: {suffix or '(no extension)'}")

        if not text.strip():
            raise RagError("document contains no extractable text")
        return Document(source=source_path.name, text=text, metadata={"file_type": suffix[1:]})

    @staticmethod
    def parse_text(text: str, source: str = "text") -> Document:
        if not isinstance(text, str) or not text.strip():
            raise RagError("document text must be a non-empty string")
        if not isinstance(source, str) or not source.strip():
            raise RagError("document source must be a non-empty string")
        return Document(source=source.strip(), text=text, metadata={"file_type": "text"})

    @staticmethod
    def _parse_pdf(path: Path) -> str:
        try:
            from pypdf import PdfReader
        except ImportError as error:
            raise RagError("PDF parsing requires the optional 'pypdf' package") from error
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(page for page in pages if page.strip())
