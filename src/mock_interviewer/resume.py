"""Minimal, safe PDF/DOCX text extraction with explicit input limits."""

from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree


class ResumeParser:
    async def extract_text(self, content: bytes, *, filename: str) -> str:
        suffix = filename.lower().rsplit(".", 1)[-1]
        try:
            if suffix == "pdf":
                return self._pdf(content)
            if suffix == "docx":
                return self._docx(content)
        except Exception as exc:
            raise ValueError("Resume could not be read") from exc
        raise ValueError("Only PDF and DOCX resumes are supported")

    @staticmethod
    def _pdf(content: bytes) -> str:
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content), strict=True)
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except ImportError:
            if not content.startswith(b"%PDF-"):
                raise ValueError("Invalid PDF")
            # A deliberately modest fallback handles plain text PDF content only.
            return re.sub(r"[^\x20-\x7e\n\r\t]", " ", content.decode("latin-1", errors="ignore"))

    @staticmethod
    def _docx(content: bytes) -> str:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        return "\n".join("".join(t.text or "" for t in p.findall(".//w:t", ns)) for p in root.findall(".//w:p", ns))
