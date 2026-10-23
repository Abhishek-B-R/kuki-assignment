"""PDF text layer extraction and rendering (PyMuPDF)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz

# Pages with fewer words than this are treated as image-only (scans, posters, covers);
# the extractor then relies on the page image the model sees.
IMAGE_ONLY_WORDS = 20


class PdfError(Exception):
    """The file is not a readable PDF."""


@dataclass
class PdfPage:
    page_no: int  # 1-based
    text: str  # spatially sorted (top-to-bottom, left-to-right): keeps table rows together
    word_count: int
    width: float
    height: float
    reading_text: str = ""  # content-stream order: keeps multi-column prose together

    @property
    def verify_text(self) -> str:
        """Both orderings, so a quote taken in either reading order can be found."""
        return f"{self.text}\n{self.reading_text}"


def parse_pdf(path: Path) -> list[PdfPage]:
    try:
        doc = fitz.open(path)
    except Exception as exc:  # fitz raises several types for corrupt input
        raise PdfError(f"Cannot open PDF: {exc}") from exc
    try:
        if doc.needs_pass:
            raise PdfError("PDF is password protected")
        if doc.page_count == 0:
            raise PdfError("PDF has no pages")
        pages = []
        for i, page in enumerate(doc):
            text = page.get_text("text", sort=True)
            pages.append(
                PdfPage(
                    page_no=i + 1,
                    text=text,
                    word_count=len(page.get_text("words")),
                    width=page.rect.width,
                    height=page.rect.height,
                    reading_text=page.get_text("text"),
                )
            )
        return pages
    finally:
        doc.close()


def render_page_png(path: Path, page_no: int, zoom: float = 1.6) -> bytes:
    with fitz.open(path) as doc:
        if not 1 <= page_no <= doc.page_count:
            raise IndexError(page_no)
        pix = doc[page_no - 1].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("png")


def find_boxes(path: Path, page_no: int, needles: list[str]) -> list[list[float]]:
    """Locate the first needle that appears on the page. Returns boxes as fractions of the
    page size ([x0, y0, x1, y1] in 0..1) so the dashboard can overlay them on any render size."""
    with fitz.open(path) as doc:
        if not 1 <= page_no <= doc.page_count:
            return []
        page = doc[page_no - 1]
        w, h = page.rect.width, page.rect.height
        for needle in needles:
            needle = " ".join(needle.split())
            if len(needle) < 2:
                continue
            rects = page.search_for(needle)
            if rects:
                return [[r.x0 / w, r.y0 / h, r.x1 / w, r.y1 / h] for r in rects[:6]]
    return []
