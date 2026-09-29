"""Text-layer extraction with coordinates, so every value can be traced back to a page region."""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import pdfplumber

LINE_Y_TOLERANCE = 3.0  # points; words within this vertical distance form one line
MIN_CHARS_FOR_TEXT_LAYER = 20


@dataclass
class TextLine:
    text: str
    page: int  # 1-based
    bbox: list[float]  # x0, top, x1, bottom
    words: list[dict] = field(default_factory=list)


@dataclass
class PdfText:
    page_count: int
    lines: list[TextLine]
    page_sizes: list[tuple[float, float]]

    @property
    def has_text_layer(self) -> bool:
        return sum(len(line.text) for line in self.lines) >= MIN_CHARS_FOR_TEXT_LAYER

    def full_text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    def page_text(self, page: int) -> str:
        return "\n".join(line.text for line in self.lines if line.page == page)


def read_pdf(content: bytes) -> PdfText:
    lines: list[TextLine] = []
    sizes: list[tuple[float, float]] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            sizes.append((float(page.width), float(page.height)))
            words = page.extract_words(keep_blank_chars=False, use_text_flow=False, x_tolerance=1.5)
            lines.extend(_group_lines(words, page_no))
        return PdfText(page_count=len(pdf.pages), lines=lines, page_sizes=sizes)


def _group_lines(words: list[dict], page_no: int) -> list[TextLine]:
    words = sorted(words, key=lambda w: (round(w["top"]), w["x0"]))
    groups: list[list[dict]] = []
    for w in words:
        if groups and abs(groups[-1][0]["top"] - w["top"]) <= LINE_Y_TOLERANCE:
            groups[-1].append(w)
        else:
            groups.append([w])
    out = []
    for g in groups:
        g.sort(key=lambda w: w["x0"])
        text = " ".join(w["text"] for w in g)
        bbox = [
            min(w["x0"] for w in g),
            min(w["top"] for w in g),
            max(w["x1"] for w in g),
            max(w["bottom"] for w in g),
        ]
        out.append(TextLine(text=text, page=page_no, bbox=[round(v, 1) for v in bbox], words=g))
    return out


def render_page_png(content: bytes, page: int, highlights: list[list[float]] | None = None,
                    resolution: int = 110) -> bytes:
    """Render one page as PNG with optional highlighted regions (for 'Toon factuurpagina')."""
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        if page < 1 or page > len(pdf.pages):
            raise ValueError("page out of range")
        p = pdf.pages[page - 1]
        img = p.to_image(resolution=resolution)
        for bbox in highlights or []:
            x0, top, x1, bottom = bbox
            img.draw_rect((x0 - 3, top - 3, x1 + 8, bottom + 3), fill=(255, 200, 0, 60),
                          stroke=(220, 30, 30), stroke_width=2)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
