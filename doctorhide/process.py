# -*- coding: utf-8 -*-
"""Оркестрация: process(), имена выходных файлов, default_enabled."""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

from . import config
from .config import (
    SkipFile,
    TEXT_EXT, SHEET_EXT, DOC_EXT, SUPPORTED_EXT, IMAGE_EXT,
)
from .categories import CATEGORIES, JUDICIAL_KEEP
from .text_model import Line, lines_from_text
from .names import find_names, SURNAME_STRICT, NAME_STOP
from .rules import RULES, validate, is_court_act
from .detect import find_hits
from .anonymize import anonymize_text
from .converters import (
    anonymize_docx, anonymize_xlsx, docx_to_md, sheet_to_md,
    legacy_office_to_text, legacy_office_to_docx,
)
from .pdf import redact_pdf


NAME_DATE = re.compile(r"\b\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}\b|\b(?:19|20)\d{2}\b")
# уменьшительные имена, которых нет в справочнике полных имён
NAME_HINTS = re.compile(
    r"\b(?:Саш|Маш|Миш|Пет|Ван|Дим|Кол|Ол|Лен|Тан|Наташ|Сер[её]ж|Андрюш|Юр|Вит|Вов|"
    r"Л[её]ш|Гриш|Даш|Кат|Сон|Лид|Гал|Вер|Над|Люб|Свет|Ирин|Павл|Слав|Стас|Тол|Фед|"
    r"Эдик|Игор|Оксан|Полин|Алин|Ксюш|Юл)\w*",
    re.I,
)


def sanitize_filename(stem: str, enabled: set, mode: str):
    """Убирает из имени файла то же, что скрываем в тексте, и даты.

    Возвращает (новое_имя, было_ли_что_убирать).
    """
    del mode
    spans = []
    for cat, rid, pat, val, grp in RULES:
        if cat not in enabled:
            continue
        try:
            rx = re.compile(pat, re.I)
        except re.error:
            continue
        for m in rx.finditer(stem):
            if val and not validate(val, m.group()):
                continue
            a, b = (m.start(grp), m.end(grp)) if grp else (m.start(), m.end())
            if a < 0 or b < 0:
                a, b = m.start(), m.end()
            spans.append((a, b))
    for m in NAME_DATE.finditer(stem):
        spans.append((m.start(), m.end()))
    for a, b in find_names(Line(stem, [], [])):
        spans.append((a, b))
    for m in NAME_HINTS.finditer(stem):
        if m.group()[:1].isupper():
            spans.append((m.start(), m.end()))
    # фамилия отдельным словом: «Иванов.pdf» тоже не должен оставаться в имени файла
    for m in re.finditer(r"[А-ЯЁ][А-Яа-яЁё\-]{3,}", stem):
        low = m.group().lower()
        if low in NAME_STOP:
            continue
        if SURNAME_STRICT.search(low):
            spans.append((m.start(), m.end()))
    if not spans:
        return stem, False

    spans.sort()
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))

    parts, cur = [], 0
    for a, b in merged:
        parts.append(stem[cur:a])
        cur = b
    parts.append(stem[cur:])
    clean = " ".join("".join(parts).replace("_", " ").split())
    clean = re.sub(r"^[\s\-–—.,;:]+|[\s\-–—.,;:]+$", "", clean)
    if len(re.sub(r"\W", "", clean)) < 3:
        clean = "document"
    return clean, True


def output_name(name: str, ext: str, enabled: set, mode: str) -> str:
    """Итоговое имя: всегда суффикс _hidden."""
    stem = os.path.splitext(name)[0]
    clean, _changed = sanitize_filename(stem, enabled, mode)
    return f"{clean}_hidden{ext}"


def _claim(dst: str) -> str:
    """Уникальное имя результата в пределах одного прогона (иначе files перезаписываются)."""
    used = config.CURRENT.setdefault("outputs", set())
    if dst not in used:
        used.add(dst)
        return dst
    stem, ext = os.path.splitext(dst)
    n = 2
    while f"{stem}_{n}{ext}" in used:
        n += 1
    new = f"{stem}_{n}{ext}"
    used.add(new)
    return new


def out_name(src: str, ext: str) -> str:
    """Имя результата: всегда <имя>_hidden.<ext>, без варианта _anon."""
    base = os.path.basename(src)
    if config.KEEP_NAME:
        stem = os.path.splitext(base)[0]
        return _claim(f"{stem}_hidden{ext}")
    enabled = config.CURRENT.get("enabled") or set(CATEGORIES)
    mode = config.CURRENT.get("mode") or "document"
    return _claim(output_name(base, ext, enabled, mode))


def default_enabled(mode: str, args) -> set:
    enabled = set(CATEGORIES)
    if mode == "judicial":
        enabled -= JUDICIAL_KEEP
    if args.only:
        enabled &= set(args.only)
    if args.keep:
        enabled -= set(args.keep)
    return enabled




def _process_image(path: str, args, enabled):
    """Изображение: OCR Indra, PDF с плашками.

    EXIF-метаданные исходного изображения (GPS, даты, Artist, Copyright и др.)
    извлекаются как текст и анонимизируются вместе с OCR-текстом. В выходной PDF
    EXIF не попадает (PyMuPDF insert_image не переносит EXIF), но статистика
    попадает в отчёт, чтобы пользователь видел, что было закрыто.
    """
    from .ocr import ocr_image_upright, available as ocr_available
    from .metadata import extract_image_exif_text, clear_pdf_metadata
    if not ocr_available():
        raise SkipFile("изображение: установите indra-ocr")
    enabled = set(enabled) if enabled is not None else default_enabled("document", args)
    lines, png, w, h = ocr_image_upright(path)
    if not lines:
        raise SkipFile("OCR не нашёл текст")
    # EXIF-метаданные: извлекаем один раз, используем и для judicial-режима,
    # и для статистики анонимизации.
    exif_text = extract_image_exif_text(path)
    mode = args.mode
    if mode == "auto":
        raw = "\n".join(ln.text for ln in lines)
        if exif_text:
            raw = exif_text + "\n\n" + raw
        mode = "judicial" if is_court_act(raw) else "document"
        enabled = set(enabled) if getattr(args, "keep", None) or getattr(args, "only", None) else default_enabled(mode, args)
    from .detect import find_hits
    hits, stats = find_hits(lines, enabled, mode, aggressive_finance=True)
    # Анонимизация EXIF-метаданных: координаты GPS, даты, Artist, Copyright
    # и др. закрываются [censored] через правила gps_coord, exif_date, exif_field.
    if exif_text:
        from .anonymize import anonymize_text
        _, exif_stats = anonymize_text(exif_text, mode, enabled)
        for k, v in exif_stats.items():
            stats[k] = stats.get(k, 0) + v
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page(width=w, height=h)
    if png:
        page.insert_image(pymupdf.Rect(0, 0, w, h), stream=png)
    else:
        page.insert_image(pymupdf.Rect(0, 0, w, h), filename=path)
    if hits:
        for r, _cat in hits:
            page.add_redact_annot(pymupdf.Rect(r.x0, r.y0, r.x1, r.y1), fill=(0, 0, 0))
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)
    dst = args.out or out_name(path, ".pdf")
    if not dst.lower().endswith(".pdf"):
        dst = os.path.splitext(dst)[0] + ".pdf"
    # Очистка метаданных PDF: doc.info + XMP + Information Dictionary.
    clear_pdf_metadata(doc)
    doc.save(dst, deflate=True)
    doc.close()
    config.CURRENT["enabled"], config.CURRENT["mode"] = enabled, mode
    return {"file": os.path.basename(path), "status": "ok", "mode": mode,
            "categories": stats, "kept_categories": sorted(set(CATEGORIES) - enabled),
            "output": dst}


def process(path: str, args, enabled: Optional[set] = None):
    ext = os.path.splitext(path)[1].lower()

    if ext in IMAGE_EXT:
        return _process_image(path, args, enabled)
    if ext not in SUPPORTED_EXT:
        raise SkipFile(f"формат не поддерживается ({ext or 'без расширения'})")

    mode = args.mode
    keep_fmt = getattr(args, "keep_format", False)

    # --- PDF (только с текстовым слоем) ---
    if ext == ".pdf":
        enabled = set(enabled) if enabled is not None else default_enabled("document", args)
        import pymupdf
        try:
            doc = pymupdf.open(path)
            full = "".join(p.get_text() for p in doc).strip()
            doc.close()
        except Exception as err:  # noqa: BLE001 — битый файл, не PDF, шифрован
            raise SkipFile(f"не удалось открыть PDF: {type(err).__name__}") from err
        # сканы обрабатывает redact_pdf через Indra OCR
        if mode == "auto":
            mode = "judicial" if is_court_act(full) else "document"
            enabled = set(enabled) if args.keep or args.only else default_enabled(mode, args)
        dst = args.out or out_name(path, ".pdf")
        if not dst.lower().endswith(".pdf"):
            dst = os.path.splitext(dst)[0] + ".pdf"
        stats = redact_pdf(path, dst, enabled, mode)
        config.CURRENT["enabled"], config.CURRENT["mode"] = enabled, mode
        return {"file": os.path.basename(path), "status": "ok", "mode": mode,
                "categories": stats, "kept_categories": sorted(set(CATEGORIES) - enabled),
                "output": dst}

    # --- DOC / RTF: конвертация через LibreOffice ---
    legacy_tmp_docx = None
    if ext in {".doc", ".rtf"}:
        try:
            if keep_fmt:
                legacy_tmp_docx = legacy_office_to_docx(path)
                # дальше как docx keep-format
                path_for_doc = legacy_tmp_docx
                ext = ".docx"
            else:
                raw = legacy_office_to_text(path)
                # Битый .doc LibreOffice «конвертирует» в мусор из байтов:
                # настоящий документ всегда содержит слово из 3+ букв.
                if not raw.strip() or not re.search(r"[A-Za-zА-Яа-яЁё]{3,}", raw):
                    raise SkipFile(
                        "LibreOffice вернул не текст — файл повреждён или пуст"
                    )
                if mode == "auto":
                    mode = "judicial" if is_court_act(raw) else "document"
                enabled = set(enabled) if enabled is not None else default_enabled(mode, args)
                if getattr(args, "ask", False):
                    from .cli import ask_categories
                    _, found = find_hits(lines_from_text(raw), enabled, mode)
                    enabled = ask_categories(found)
                anon, stats = anonymize_text(raw, mode, enabled)
                config.CURRENT["enabled"], config.CURRENT["mode"] = enabled, mode
                dst = args.out or out_name(path, ".md")
                with open(dst, "w", encoding="utf-8") as f:
                    f.write(anon)
                return {"file": os.path.basename(path), "status": "ok", "mode": mode,
                        "categories": stats, "kept_categories": sorted(set(CATEGORIES) - enabled),
                        "output": dst}
        except RuntimeError as err:
            raise SkipFile(str(err)) from err
    else:
        path_for_doc = path

    # --- --keep-format DOCX ---
    if keep_fmt and ext == ".docx":
        src_docx = path_for_doc if ext == ".docx" else path
        try:
            if mode == "auto":
                raw_probe = docx_to_md(src_docx)
                mode = "judicial" if is_court_act(raw_probe) else "document"
            enabled = set(enabled) if enabled is not None else default_enabled(mode, args)
            if getattr(args, "ask", False):
                from .cli import ask_categories
                _, found = find_hits(lines_from_text(docx_to_md(src_docx)), enabled, mode)
                enabled = ask_categories(found)
            dst = args.out or out_name(path, ".docx")
            if not dst.lower().endswith(".docx"):
                dst = os.path.splitext(dst)[0] + ".docx"
            stats = anonymize_docx(src_docx, dst, mode, enabled)
            config.CURRENT["enabled"], config.CURRENT["mode"] = enabled, mode
            return {"file": os.path.basename(path), "status": "ok", "mode": mode,
                    "categories": stats, "kept_categories": sorted(set(CATEGORIES) - enabled),
                    "output": dst}
        finally:
            if legacy_tmp_docx:
                import shutil
                shutil.rmtree(os.path.dirname(legacy_tmp_docx), ignore_errors=True)

    if keep_fmt and ext in SHEET_EXT:
        if mode == "auto":
            raw_probe = sheet_to_md(path)
            mode = "judicial" if is_court_act(raw_probe) else "document"
        enabled = set(enabled) if enabled is not None else default_enabled(mode, args)
        if getattr(args, "ask", False):
            from .cli import ask_categories
            _, found = find_hits(lines_from_text(sheet_to_md(path)), enabled, mode)
            enabled = ask_categories(found)
        dst = args.out or out_name(path, ".xlsx")
        if not dst.lower().endswith((".xlsx", ".xls")):
            dst = os.path.splitext(dst)[0] + ".xlsx"
        stats = anonymize_xlsx(path, dst, mode, enabled)
        config.CURRENT["enabled"], config.CURRENT["mode"] = enabled, mode
        return {"file": os.path.basename(path), "status": "ok", "mode": mode,
                "categories": stats, "kept_categories": sorted(set(CATEGORIES) - enabled),
                "output": dst}

    # --- текстовый путь (docx→md, xlsx→md, txt/md) ---
    if ext in DOC_EXT or ext == ".docx":
        src = path_for_doc if path_for_doc else path
        try:
            raw = docx_to_md(src)
        finally:
            if legacy_tmp_docx:
                import shutil
                shutil.rmtree(os.path.dirname(legacy_tmp_docx), ignore_errors=True)
        out_ext = ".md"
    elif ext in SHEET_EXT:
        raw = sheet_to_md(path)
        if not raw.strip():
            raise SkipFile("в таблице нет данных")
        rows, heads = [], {}
        for line in raw.split("\n"):
            if line.startswith("|") and not re.search(r"\d", line):
                key = f"HDR{len(heads)}X"
                heads[key] = line
                rows.append(key)
                continue
            rows.append(line)
        saved_heads = heads
        raw = "\n".join(rows)
        out_ext = ".md"
    else:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        out_ext = ext

    if mode == "auto":
        mode = "judicial" if is_court_act(raw) else "document"
    enabled = set(enabled) if enabled is not None else default_enabled(mode, args)
    if getattr(args, "ask", False):
        from .cli import ask_categories
        _, found = find_hits(lines_from_text(raw), enabled, mode)
        enabled = ask_categories(found)
    anon, stats = anonymize_text(raw, mode, enabled)
    if ext in SHEET_EXT:
        for key, original in saved_heads.items():
            anon = anon.replace(key, original) if key in anon else anon
            anon = re.sub(r"\[censored\]\s*" + key, original, anon)
    config.CURRENT["enabled"], config.CURRENT["mode"] = enabled, mode
    dst = args.out or out_name(path, out_ext)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(anon)
    return {"file": os.path.basename(path), "status": "ok", "mode": mode,
            "categories": stats, "kept_categories": sorted(set(CATEGORIES) - enabled),
            "output": dst}
