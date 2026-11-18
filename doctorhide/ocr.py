# -*- coding: utf-8 -*-
"""OCR через Indra: сканы PDF и изображения.

Рендерим страницу сами при известном DPI, чтобы bbox точно
ложились на пункты PDF. Строки режем на слова с оценкой позиций,
слабые распознавания отбрасываем.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from .text_model import Line, Rect, Word

_OCR = None
_DPI = 200
_MIN_CONF = 0.35


def _engine():
    """Ленивая загрузка распознавателя Indra."""
    global _OCR
    if _OCR is None:
        import indra_ocr
        _OCR = indra_ocr.OrtOCR(indra_ocr.model_dir(None), threads=4)
    return _OCR


def available() -> bool:
    try:
        import indra_ocr  # noqa: F401
        return True
    except ImportError:
        return False


def _split_words(text: str, x0: float, x1: float, y0: float, y1: float) -> List[Word]:
    """Делит строку на слова, распределяя ширину пропорционально длине."""
    parts = text.split()
    if not parts:
        return []
    total = sum(len(p) for p in parts) or 1
    width = x1 - x0
    words: List[Word] = []
    cursor = x0
    gap = width * 0.02 / max(len(parts), 1)
    for i, part in enumerate(parts):
        w = width * (len(part) / total)
        if i == len(parts) - 1:
            end = x1
        else:
            end = cursor + w
        words.append(Word(part, Rect(cursor, y0, end, y1)))
        cursor = end + gap
    return words


def _page_to_lines(page, scale: float, page_w: float, page_h: float) -> List[Line]:
    """Indra Page → наши Line. scale = пикселей на пункт PDF."""
    out: List[Line] = []
    for ln in page.lines or []:
        text = (ln.text or "").strip()
        if not text or (ln.conf or 0) < _MIN_CONF:
            continue
        x0, y0, x1, y1 = ln.box
        rx0 = max(0.0, x0 / scale)
        ry0 = max(0.0, y0 / scale)
        rx1 = min(page_w, x1 / scale)
        ry1 = min(page_h, y1 / scale)
        if rx1 - rx0 < 1 or ry1 - ry0 < 1:
            continue
        words = _split_words(text, rx0, rx1, ry0, ry1)
        if not words:
            continue
        # starts — позиции слов в склеенной строке
        starts, pos = [], 0
        for i, w in enumerate(words):
            if i:
                pos += 1
            starts.append(pos)
            pos += len(w.text)
        out.append(Line(text, words, starts))
    return out


def ocr_pdf_pages(path: str) -> List[List[Line]]:
    """OCR PDF-скана. Координаты в пунктах PDF."""
    import pymupdf
    import numpy as np
    ocr = _engine()
    doc = pymupdf.open(path)
    pages: List[List[Line]] = []
    scale = _DPI / 72.0
    for page in doc:
        pix = page.get_pixmap(dpi=_DPI, colorspace=pymupdf.csGRAY)
        gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        result = ocr(gray, turn=True)
        pages.append(_page_to_lines(result, scale, page.rect.width, page.rect.height))
    doc.close()
    return pages


def ocr_image(path: str) -> List[Line]:
    """OCR изображения. Координаты в пикселях изображения."""
    import cv2
    ocr = _engine()
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return []
    result = ocr(img, turn=True)
    h, w = img.shape[:2]
    # scale=1: координаты уже в пикселях
    return _page_to_lines(result, 1.0, float(w), float(h))


def ocr_pdf_with_tables(path: str):
    """OCR PDF: строки и прямоугольники таблиц (в пунктах PDF).

    Возвращает (pages_lines, table_rects_per_page).
    """
    import pymupdf
    import numpy as np
    ocr = _engine()
    doc = pymupdf.open(path)
    pages: List[List[Line]] = []
    tables: List[List[Rect]] = []
    scale = _DPI / 72.0
    for page in doc:
        pix = page.get_pixmap(dpi=_DPI, colorspace=pymupdf.csGRAY)
        gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        result = ocr(gray, turn=True)
        pages.append(_page_to_lines(result, scale, page.rect.width, page.rect.height))
        trects = []
        for b in result.blocks or []:
            if b.get("label") == "Table":
                x0, y0, x1, y1 = b["bbox"]
                trects.append(Rect(
                    max(0.0, x0 / scale), max(0.0, y0 / scale),
                    min(page.rect.width, x1 / scale), min(page.rect.height, y1 / scale),
                ))
        tables.append(trects)
    doc.close()
    return pages, tables
