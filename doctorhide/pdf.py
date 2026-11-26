# -*- coding: utf-8 -*-
"""PDF: извлечение строк, плашки, redaction (с учётом page.rotation)."""

from __future__ import annotations

from typing import Dict, List, Tuple

from .config import SkipFile
from .detect import find_hits
from .ocr import ocr_pdf_pages, ocr_pdf_with_tables, available as ocr_available
from .text_model import Line, Rect, Word, _group_lines
from .vertical_text import regroup_words_with_vertical


def _pymupdf():
    import pymupdf
    return pymupdf


def _transform_rect(rect: Rect, matrix) -> Rect:
    """Применяет matrix к четырём углам Rect, возвращает axis-aligned bbox."""
    import pymupdf

    # В PyMuPDF 2.0 точку преобразует только `point * matrix` (обратный порядок
    # бросает ValueError: Matrix: bad seq len).
    pts = [
        pymupdf.Point(rect.x0, rect.y0) * matrix,
        pymupdf.Point(rect.x1, rect.y0) * matrix,
        pymupdf.Point(rect.x0, rect.y1) * matrix,
        pymupdf.Point(rect.x1, rect.y1) * matrix,
    ]
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
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


def _redact_scan(path: str, dst: str, enabled: set, mode: str, pymupdf) -> Dict[str, int]:
    """Скан без текстового слоя: страницы пересобираются выпрямленными.

    Так работает и для сканов, положенных набок, и для страниц с /Rotate:
    отрисованная страница OCR-ится как есть, при необходимости поворачивается
    в нормальную ориентацию и плашки рисуются прямо в пикселях.
    """
    import cv2
    import numpy as np

    from .ocr import _DPI, _engine, _page_to_lines, _rotate_cw, ocr_page

    src = pymupdf.open(path)
    out = pymupdf.open()
    stats: Dict[str, int] = {}
    scale = _DPI / 72.0
    for page in src:
        pix = page.get_pixmap(dpi=_DPI, colorspace=pymupdf.csGRAY)
        gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()
        result = ocr_page(gray)
        upright = _rotate_cw(gray, result.rotation)
        h, w = upright.shape[:2]
        lines = _page_to_lines(result, 1.0, float(w), float(h))
        hits, page_stats = find_hits(lines, enabled, mode, aggressive_finance=True)
        for cat, n in page_stats.items():
            stats[cat] = stats.get(cat, 0) + n
        for b in bars([r for r, _ in hits]):
            cv2.rectangle(
                upright,
                (max(0, int(b.x0) - 2), max(0, int(b.y0) - 2)),
                (min(w - 1, int(b.x1) + 2), min(h - 1, int(b.y1) + 2)),
                0, -1,
            )
        ok, buf = cv2.imencode(".jpg", upright, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        new_page = out.new_page(width=w / scale, height=h / scale)
        new_page.insert_image(new_page.rect, stream=buf.tobytes())
    out.set_metadata({})
    # Дополнительно: очистка XMP и Information Dictionary.
    from .metadata import clear_pdf_metadata
    clear_pdf_metadata(out)
    out.save(dst, garbage=4, deflate=True)
    out.close()
    src.close()
    return stats


def _sanitize_form_fields(page, enabled: set, mode: str, stats: Dict[str, int]) -> None:
    """Значения полей форм (AcroForm).

    get_text() их не видит, apply_redactions их не трогает — без этого шага
    «обезличенный» PDF продолжает отдавать ФИО, адрес и телефон из полей.
    """
    from .anonymize import anonymize_text

    try:
        widgets = list(page.widgets() or [])
    except Exception:  # noqa: BLE001 — битые виджеты не должны ронять файл
        return
    for widget in widgets:
        try:
            value = widget.field_value
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        anon, local = anonymize_text(value, mode, enabled)
        if anon == value:
            continue
        try:
            widget.field_value = anon
            widget.update()
        except Exception:  # noqa: BLE001 — поле только для чтения и т.п.
            continue
        for cat, n in local.items():
            stats[cat] = stats.get(cat, 0) + n


def _sanitize_annotations(page, enabled: set, mode: str, stats: Dict[str, int]) -> None:
    """Текст в аннотациях: заметки, комментарии, штампы, всплывающие подсказки."""
    from .anonymize import anonymize_text

    try:
        annots = list(page.annots() or [])
    except Exception:  # noqa: BLE001
        return
    for annot in annots:
        try:
            info = dict(annot.info or {})
        except Exception:  # noqa: BLE001
            continue
        changed = {}
        for key in ("content", "title", "subject", "author"):
            value = info.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            anon, local = anonymize_text(value, mode, enabled)
            if anon == value:
                continue
            changed[key] = anon
            for cat, n in local.items():
                stats[cat] = stats.get(cat, 0) + n
        if not changed:
            continue
        try:
            # Только изменённые поля: остальные ключи set_info не должен трогать.
            annot.set_info(changed)
            annot.update()
        except Exception:  # noqa: BLE001
            continue


def _drop_attachments(doc) -> int:
    """Вложенные файлы: на странице не видны, но уходят в копию как есть."""
    removed = 0
    try:
        names = list(doc.embfile_names() or [])
    except Exception:  # noqa: BLE001
        return 0
    for name in names:
        try:
            doc.embfile_del(name)
            removed += 1
        except Exception:  # noqa: BLE001
            continue
    return removed


def redact_pdf(path: str, dst: str, enabled: set, mode: str) -> Dict[str, int]:
    """PDF: чёрные полосы. Сканы без текстового слоя — через Indra OCR."""
    pymupdf = _pymupdf()
    try:
        doc = pymupdf.open(path)
        full = "".join(p.get_text() for p in doc).strip()
    except Exception as err:  # noqa: BLE001 — битый файл, не PDF, шифрован
        raise SkipFile(f"не удалось открыть PDF: {type(err).__name__}") from err
    # Скан — это картинка во всю страницу; короткий текст без картинок — обычный PDF
    # (иначе однострочный текстовый PDF уходил в «скан» и пропускался).
    is_scan = len(full) < 40 and any(p.get_images(full=True) for p in doc)
    if is_scan:
        doc.close()
        if not ocr_available():
            raise SkipFile("pdf без текстового слоя (установите indra-ocr)")
        return _redact_scan(path, dst, enabled, mode, pymupdf)
    stats: Dict[str, int] = {}
    for page in doc:
        lines = pdf_lines(page)
        hits, page_stats = find_hits(
            lines, enabled, mode, aggressive_finance=True
        )
        # pdf_lines отдаёт координаты видимой (повёрнутой) страницы, а плашки
        # ставятся в неповёрнутой системе координат PDF.
        if page.rotation:
            hits = [(_transform_rect(r, page.derotation_matrix), cat) for r, cat in hits]
        for cat, n in page_stats.items():
            stats[cat] = stats.get(cat, 0) + n
        # Поля форм и аннотации в текстовом слое не видны — обрабатываем отдельно.
        _sanitize_form_fields(page, enabled, mode, stats)
        _sanitize_annotations(page, enabled, mode, stats)
        if hits:
            _apply_bars(page, [r for r, _ in hits], pymupdf, scan=False)
    _drop_attachments(doc)
    doc.set_metadata({})
    # Дополнительно: очистка XMP и Information Dictionary.
    from .metadata import clear_pdf_metadata
    clear_pdf_metadata(doc)
    doc.save(dst, garbage=4, deflate=True, clean=True)
    doc.close()
    return stats
