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


def _unrotate(box, rotation: int, width: int, height: int):
    """Координаты выпрямленного изображения -> координаты исходного.

    Движок поворачивает картинку по часовой стрелке на `rotation` градусов;
    плашки нужно вернуть туда, где текст лежит на исходной странице.
    width/height — размеры ИСХОДНОГО изображения.
    """
    r = int(rotation or 0) % 360
    x0, y0, x1, y1 = box
    if r == 90:
        pts = [(y, height - x) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    elif r == 180:
        pts = [(width - x, height - y) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    elif r == 270:
        pts = [(width - y, x) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    else:
        return box
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _page_to_lines(page, scale: float, page_w: float, page_h: float,
                   rotation: int = 0, img_w: int = 0, img_h: int = 0) -> List[Line]:
    """Indra Page → наши Line. scale = пикселей на пункт PDF."""
    out: List[Line] = []
    for ln in page.lines or []:
        text = (ln.text or "").strip()
        if not text or (ln.conf or 0) < _MIN_CONF:
            continue
        x0, y0, x1, y1 = _unrotate(ln.box, rotation, img_w, img_h)
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
        h, w = gray.shape[:2]
        pages.append(_page_to_lines(result, scale, page.rect.width, page.rect.height,
                                    rotation=result.rotation, img_w=w, img_h=h))
    doc.close()
    return pages


def _rotate_cw(gray, rotation: int):
    """Поворачивает изображение по часовой стрелке (как это делает движок OCR)."""
    import cv2

    r = int(rotation or 0) % 360
    if r == 90:
        return cv2.rotate(gray, cv2.ROTATE_90_CLOCKWISE)
    if r == 180:
        return cv2.rotate(gray, cv2.ROTATE_180)
    if r == 270:
        return cv2.rotate(gray, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return gray


def ocr_image_upright(path: str):
    """OCR изображения с выпрямлением: (строки, PNG-байты, ширина, высота).

    Координаты строк — в пикселях уже выпрямленной картинки.
    """
    import cv2

    ocr = _engine()
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return [], None, 0, 0
    result = ocr(img, turn=True)
    up = _rotate_cw(img, result.rotation)
    h, w = up.shape[:2]
    lines = _page_to_lines(result, 1.0, float(w), float(h))
    ok, buf = cv2.imencode(".png", up)
    return lines, (buf.tobytes() if ok else None), w, h


def ocr_image(path: str) -> List[Line]:
    """OCR изображения. Координаты в пикселях исходного изображения."""
    import cv2
    ocr = _engine()
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return []
    result = ocr(img, turn=True)
    h, w = img.shape[:2]
    # scale=1: координаты уже в пикселях; поворот возвращаем к исходной картинке
    return _page_to_lines(result, 1.0, float(w), float(h),
                          rotation=result.rotation, img_w=w, img_h=h)


def ocr_pdf_with_tables(path: str):
    """OCR PDF: строки, прямоугольники таблиц и поворот каждой страницы.

    Возвращает (pages_lines, table_rects_per_page, rotations).
    """
    import pymupdf
    import numpy as np
    ocr = _engine()
    doc = pymupdf.open(path)
    pages: List[List[Line]] = []
    tables: List[List[Rect]] = []
    rotations: List[int] = []
    scale = _DPI / 72.0
    for page in doc:
        pix = page.get_pixmap(dpi=_DPI, colorspace=pymupdf.csGRAY)
        gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        result = ocr(gray, turn=True)
        h, w = gray.shape[:2]
        pages.append(_page_to_lines(result, scale, page.rect.width, page.rect.height,
                                    rotation=result.rotation, img_w=w, img_h=h))
        trects = []
        for b in result.blocks or []:
            if b.get("label") == "Table":
                x0, y0, x1, y1 = _unrotate(b["bbox"], result.rotation, w, h)
                trects.append(Rect(
                    max(0.0, x0 / scale), max(0.0, y0 / scale),
                    min(page.rect.width, x1 / scale), min(page.rect.height, y1 / scale),
                ))
        tables.append(trects)
        rotations.append(int(result.rotation or 0) % 360)
    doc.close()
    return pages, tables, rotations
