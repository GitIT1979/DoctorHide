# -*- coding: utf-8 -*-
"""PDF: извлечение строк, плашки, redaction (с учётом page.rotation)."""

from __future__ import annotations

from typing import Dict, List, Tuple

from .config import SkipFile
from .detect import find_hits
from .ocr import ocr_pdf_pages, ocr_pdf_with_tables, available as ocr_available
from .text_model import Line, Rect, Word, _group_lines
from .table_layout import TableBlock
from .vertical_text import regroup_words_with_vertical


def _pymupdf():
    import pymupdf
    return pymupdf


def _transform_rect(rect: Rect, matrix) -> Rect:
    """Применяет matrix к четырём углам Rect, возвращает axis-aligned bbox."""
    pts = [
        matrix * (rect.x0, rect.y0),
        matrix * (rect.x1, rect.y0),
        matrix * (rect.x0, rect.y1),
        matrix * (rect.x1, rect.y1),
    ]
    xs = [p.x if hasattr(p, "x") else p[0] for p in pts]
    ys = [p.y if hasattr(p, "y") else p[1] for p in pts]
    return Rect(min(xs), min(ys), max(xs), max(ys))


def pdf_lines(page) -> List[Line]:
    """Строки PDF по текстовому слою.

    Координаты get_text привязаны к неповёрнутому MediaBox. Если
    page.rotation ∈ {90,180,270}, переводим bbox через rotation_matrix,
    чтобы плашки совпали с визуальным положением текста.
    """
    rotation = int(getattr(page, "rotation", 0) or 0) % 360
    matrix = page.rotation_matrix if rotation else None

    buckets: Dict[Tuple[int, int], List[Word]] = {}
    all_words: List[Word] = []
    for x0, y0, x1, y1, w, b, l, _n in page.get_text("words"):
        if not w.strip():
            continue
        r = Rect(x0, y0, x1, y1)
        if matrix is not None:
            r = _transform_rect(r, matrix)
        word = Word(w, r)
        buckets.setdefault((b, l), []).append(word)
        all_words.append(word)

    from .vertical_text import is_vertical_word, merge_vertical_words

    vertical_merged, _horiz = merge_vertical_words(all_words)
    consumed = set()
    for mw in vertical_merged:
        for ow in all_words:
            narrow = ow.rect.width <= 14.0 and len(ow.text.strip()) <= 2
            if not (is_vertical_word(ow) or narrow):
                continue
            if (
                ow.rect.x0 >= mw.rect.x0 - 2
                and ow.rect.x1 <= mw.rect.x1 + 2
                and ow.rect.y0 >= mw.rect.y0 - 2
                and ow.rect.y1 <= mw.rect.y1 + 2
            ):
                consumed.add(id(ow))

    out: List[Line] = []
    for key in sorted(buckets):
        kept = [w for w in buckets[key] if id(w) not in consumed]
        if kept:
            out.extend(_group_lines(sorted(kept, key=lambda x: x.rect.x0)))
    for mw in vertical_merged:
        out.extend(_group_lines([mw]))
    return out


def bars(rects: List[Rect], gap: float = 4.0) -> List[Rect]:
    """Склеивает найденные слова в чёрные плашки по точным bbox слов (без запаса)."""
    if not rects:
        return []
    rows: List[List[Rect]] = []
    for r in sorted(rects, key=lambda r: (round(r.y0, 1), r.x0)):
        if rows and abs(rows[-1][0].y0 - r.y0) <= 2.0 and abs(rows[-1][0].y1 - r.y1) <= 2.5:
            rows[-1].append(r)
        else:
            rows.append([r])

    out: List[Rect] = []
    for row in rows:
        row.sort(key=lambda r: r.x0)
        x0, y0, x1, y1 = row[0].x0, row[0].y0, row[0].x1, row[0].y1
        for r in row[1:]:
            if r.x0 - x1 <= gap:
                x1 = max(x1, r.x1)
                y0 = min(y0, r.y0)
                y1 = max(y1, r.y1)
            else:
                out.append(Rect(x0 - 1.0, y0 - 0.5, x1 + 1.0, y1 + 0.5))
                x0, y0, x1, y1 = r.x0, r.y0, r.x1, r.y1
        h = y1 - y0
        if h > 28.0 and len(row) > 1:
            for r in row:
                out.append(Rect(r.x0 - 1.0, r.y0 - 0.5, r.x1 + 1.0, r.y1 + 0.5))
        else:
            out.append(Rect(x0 - 1.0, y0 - 0.5, x1 + 1.0, y1 + 0.5))
    return out


def _apply_bars(page, rects: List[Rect], pymupdf, *, scan: bool = False) -> None:
    for b in bars(rects):
        page.add_redact_annot(
            pymupdf.Rect(b.x0, b.y0, b.x1, b.y1), fill=(0, 0, 0), cross_out=False
        )
    # Для сканов (весь контент — картинка) нельзя REMOVE, иначе страница пустеет.
    img_mode = (
        pymupdf.PDF_REDACT_IMAGE_PIXELS if scan else pymupdf.PDF_REDACT_IMAGE_REMOVE
    )
    page.apply_redactions(images=img_mode)


def redact_pdf(path: str, dst: str, enabled: set, mode: str) -> Dict[str, int]:
    """PDF: чёрные полосы. Сканы без текстового слоя — через Indra OCR."""
    pymupdf = _pymupdf()
    doc = pymupdf.open(path)
    full = "".join(p.get_text() for p in doc).strip()
    # Скан — это картинка во всю страницу; короткий текст без картинок — обычный PDF
    # (иначе однострочный текстовый PDF уходил в «скан» и пропускался).
    is_scan = len(full) < 40 and any(p.get_images(full=True) for p in doc)
    ocr_pages = None
    ocr_tables = None
    if is_scan:
        if not ocr_available():
            doc.close()
            raise SkipFile("pdf без текстового слоя (установите indra-ocr)")
        ocr_pages, ocr_tables = ocr_pdf_with_tables(path)
    stats: Dict[str, int] = {}
    for i, page in enumerate(doc):
        extra = None
        if is_scan and ocr_pages is not None and i < len(ocr_pages):
            lines = ocr_pages[i]
            if ocr_tables and i < len(ocr_tables) and ocr_tables[i]:
                tb = TableBlock()
                for ln in lines:
                    for w in ln.words:
                        cx = (w.rect.x0 + w.rect.x1) / 2
                        cy = (w.rect.y0 + w.rect.y1) / 2
                        if any(r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1 for r in ocr_tables[i]):
                            tb.lines.append(ln)
                            tb.line_ids.add(id(ln))
                            break
                if tb.lines:
                    tb.money = tb.pii = True
                    extra = [tb]
        else:
            lines = pdf_lines(page)
        hits, page_stats = find_hits(
            lines, enabled, mode, aggressive_finance=True, extra_tables=extra
        )
        if not hits:
            continue
        for cat, n in page_stats.items():
            stats[cat] = stats.get(cat, 0) + n
        _apply_bars(page, [r for r, _ in hits], pymupdf, scan=is_scan)
    doc.set_metadata({})
    doc.save(dst, garbage=4, deflate=True, clean=True)
    doc.close()
    return stats
