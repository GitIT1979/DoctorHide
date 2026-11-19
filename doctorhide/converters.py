# -*- coding: utf-8 -*-
"""Конвертация DOCX / XLSX и сохранение формата при --keep-format."""

from __future__ import annotations

import contextlib
import os
import re
import shutil
from typing import Dict, List, Tuple

from .anonymize import anonymize_text
from .config import SkipFile


def _find_soffice():
    """soffice из PATH или из обычных мест установки (Windows, macOS, Linux)."""
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if exe:
        return exe
    for path in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/usr/lib/libreoffice/program/soffice",
        "/usr/bin/soffice",
    ):
        if os.path.isfile(path):
            return path
    return None


def _soffice_convert(path: str, target: str, timeout: int = 120) -> str:
    """Конвертирует файл через LibreOffice; результат — во временной папке.

    Удалять временную папку — забота вызывающего (os.path.dirname результата).
    """
    import subprocess
    import tempfile

    soffice = _find_soffice()
    if not soffice:
        raise RuntimeError(
            "нужен LibreOffice (soffice) — установите его или добавьте в PATH"
        )
    tmp = tempfile.mkdtemp()
    proc = subprocess.run(
        [soffice, "--headless", "--nologo", "--nolockcheck",
         "--convert-to", target, "--outdir", tmp, path],
        capture_output=True, text=True, timeout=timeout,
    )
    ext = "." + target.split(":")[0].strip().lower()
    found = [f for f in os.listdir(tmp) if f.lower().endswith(ext)]
    if not found:
        err = (proc.stderr or proc.stdout or "").strip() or "unknown error"
        raise RuntimeError(
            f"LibreOffice не сконвертировал {os.path.splitext(path)[1]} в {target}: {err}"
        )
    return os.path.join(tmp, found[0])


@contextlib.contextmanager
def _sheet_source(path: str):
    """Для старого .xls подставляет сконвертированный .xlsx (openpyxl его не читает)."""
    if os.path.splitext(path)[1].lower() != ".xls":
        yield path
        return
    try:
        converted = _soffice_convert(path, "xlsx")
    except RuntimeError as err:
        raise SkipFile(str(err)) from err
    try:
        yield converted
    finally:
        shutil.rmtree(os.path.dirname(converted), ignore_errors=True)


def docx_to_md(path: str) -> str:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = Document(path)
    out: List[str] = []
    for child in d.element.body.iterchildren():
        if child.tag == qn("w:p"):
            p = Paragraph(child, d)
            text = p.text.strip()
            if not text:
                out.append("")
                continue
            style = (p.style.name or "").lower()
            if style.startswith("heading"):
                lvl = re.sub(r"\D", "", style) or "1"
                out.append("#" * min(int(lvl), 6) + " " + text)
            elif "list" in style or (p._p.pPr is not None and p._p.pPr.numPr is not None):
                out.append("- " + text)
            elif style.startswith("title"):
                out.append("# " + text)
            else:
                out.append(text)
        elif child.tag == qn("w:tbl"):
            t = Table(child, d)
            rows = [[c.text.strip().replace("\n", " ") for c in r.cells] for r in t.rows]
            if not rows:
                continue
            width = max(len(r) for r in rows)
            rows = [r + [""] * (width - len(r)) for r in rows]
            out.append("| " + " | ".join(rows[0]) + " |")
            out.append("|" + "|".join(["---"] * width) + "|")
            for r in rows[1:]:
                out.append("| " + " | ".join(r) + " |")
            out.append("")
    return "\n".join(out).strip() + "\n"


def sheet_to_md(path: str) -> str:
    """Таблица -> Markdown: листы как разделы, строки как строки таблицы."""
    import openpyxl

    with _sheet_source(path) as src:
        wb = openpyxl.load_workbook(src, data_only=True)
    out: List[str] = []
    for ws in wb.worksheets:
        rows = [["" if c is None else str(c) for c in r] for r in ws.iter_rows(values_only=True)]
        rows = [r for r in rows if any(x.strip() for x in r)]
        if not rows:
            continue
        out.append(f"## {ws.title}")
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        out.append("| " + " | ".join(rows[0]) + " |")
        out.append("|" + "|".join(["---"] * width) + "|")
        for r in rows[1:]:
            out.append("| " + " | ".join(r) + " |")
        out.append("")
    return "\n".join(out).strip() + "\n"


def _set_paragraph_text(paragraph, new_text: str) -> None:
    """Заменяет текст абзаца, сохраняя стиль первого run (если есть)."""
    if not paragraph.runs:
        if new_text:
            paragraph.add_run(new_text)
        return
    paragraph.runs[0].text = new_text
    for run in paragraph.runs[1:]:
        run.text = ""


def _anonymize_paragraph(paragraph, mode: str, enabled: set, stats: Dict[str, int]) -> None:
    original = paragraph.text
    if not original or not original.strip():
        return
    anon, local = anonymize_text(original, mode, enabled)
    if anon == original:
        return
    _set_paragraph_text(paragraph, anon)
    for k, v in local.items():
        stats[k] = stats.get(k, 0) + v


def anonymize_docx(path: str, dst: str, mode: str, enabled: set) -> Dict[str, int]:
    """Обезличивает DOCX на месте: структура, стили абзацев и таблиц сохраняются.

    Форматирование внутри абзаца (частичное выделение) упрощается: весь текст
    абзаца переносится в первый run.
    """
    from docx import Document

    doc = Document(path)
    stats: Dict[str, int] = {}
    for p in doc.paragraphs:
        _anonymize_paragraph(p, mode, enabled, stats)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    _anonymize_paragraph(p, mode, enabled, stats)
    # колонтитулы
    for section in doc.sections:
        for part in (
            section.header,
            section.footer,
            getattr(section, "first_page_header", None),
            getattr(section, "first_page_footer", None),
        ):
            if part is None:
                continue
            for p in part.paragraphs:
                _anonymize_paragraph(p, mode, enabled, stats)
    doc.save(dst)
    # Очистка метаданных: creator, lastModifiedBy, created, modified и т.п.
    from .metadata import clear_office_metadata
    clear_office_metadata(dst)
    return stats


def anonymize_xlsx(path: str, dst: str, mode: str, enabled: set) -> Dict[str, int]:
    """Обезличивает строковые ячейки XLSX/XLS, сохраняя книгу и типы остальных ячеек."""
    import openpyxl

    with _sheet_source(path) as src:
        wb = openpyxl.load_workbook(src)
    stats: Dict[str, int] = {}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is None:
                    continue
                if not isinstance(cell.value, str):
                    # числа в ячейках: прогоняем через is_finance только если finance включён
                    if "finance" in enabled and isinstance(cell.value, (int, float)):
                        from . import config
                        from .finance import is_finance_token

                        val = cell.value
                        if isinstance(val, float) and val == int(val):
                            tok = str(int(val))
                        else:
                            tok = str(val)
                        # без текстового контекста — только длинные / форматированные
                        if is_finance_token(tok, ""):
                            cell.value = config.PLACEHOLDER
                            stats["finance"] = stats.get("finance", 0) + 1
                    continue
                anon, local = anonymize_text(cell.value, mode, enabled)
                if anon != cell.value:
                    cell.value = anon
                    for k, v in local.items():
                        stats[k] = stats.get(k, 0) + v
    wb.save(dst)
    # Очистка метаданных: Application, Company, Manager и т.п.
    from .metadata import clear_office_metadata
    clear_office_metadata(dst)
    return stats


def legacy_office_to_text(path: str) -> str:
    """DOC / RTF → plain text через LibreOffice (soffice --headless)."""
    converted = _soffice_convert(path, "txt:Text (encoded):UTF8")
    try:
        with open(converted, "rb") as f:
            raw = f.read()
        for enc in ("utf-8-sig", "cp1251"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")
    finally:
        shutil.rmtree(os.path.dirname(converted), ignore_errors=True)


def legacy_office_to_docx(path: str) -> str:
    """DOC / RTF → временный DOCX (путь к файлу) через LibreOffice.

    Вызывающий должен удалить папку файла после использования.
    """
    return _soffice_convert(path, "docx")


def legacy_sheet_to_xlsx(path: str) -> str:
    """Старый .xls → временный .xlsx через LibreOffice.

    Вызывающий должен удалить папку файла после использования.
    """
    return _soffice_convert(path, "xlsx")
