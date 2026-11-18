# -*- coding: utf-8 -*-
"""Единая детекция персональных данных для текста и PDF."""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from .finance import is_finance_token, is_sensitive_number
from .table_layout import detect_table_blocks, line_table_flags
from .names import (
    PATRONYMIC, SURNAME_STRICT, _cyr, english_names, find_names, normalize,
)
from .rules import RULES, validate
from .text_model import Line, Rect
from .engine import find_spans

# Precompiled rules: (category, rule_id, compiled_rx, validator_kind, group_index)
def _rule_order(rule):
    cat, rid, pat, val, grp = rule
    if str(rid).startswith("court"):
        priority = 0
    elif str(rid).startswith("institution"):
        priority = 1
    else:
        priority = 2
    return (priority, -len(str(pat)))


_COMPILED: List[Tuple[str, str, re.Pattern, Optional[str], int]] = [
    (cat, rid, re.compile(pat, re.I), val, grp)
    for cat, rid, pat, val, grp in sorted(RULES, key=_rule_order)
]


def find_hits(
    lines: List[Line],
    enabled: set,
    mode: str = "document",
    *,
    only_ids: Optional[set] = None,
    aggressive_finance: bool = False,
    extra_tables: Optional[list] = None,
) -> Tuple[List[Tuple[Rect, str]], Dict[str, int]]:
    """Находит персональные данные в строках.

    Возвращает (список (Rect, category), счётчики по категориям).

    aggressive_finance=True (для PDF): числа через is_sensitive_number
    с учётом строки и табличных блоков (колонки по x). Money/PII red-flag
    в таблице → все подходящие числа в блоке. В текстовом пути флаг
    обычно выключен (там mask_numbers + markdown-таблицы).
    """
    del mode  # зарезервирован для будущих отличий judicial/document
    rects: List[Tuple[Rect, str]] = []
    stats: Dict[str, int] = {}
    # новый движок: склейка строк
    if only_ids is None:
        for r, cat in find_spans(lines):
            if cat in enabled:
                rects.append((r, cat))
                stats[cat] = stats.get(cat, 0) + 1


    compiled = [
        (cat, rid, rx, val, grp)
        for cat, rid, rx, val, grp in _COMPILED
        if cat in enabled and (only_ids is None or rid in only_ids)
    ]

    # табличные блоки по x-координатам (только если есть реальные bbox)
    table_blocks = detect_table_blocks(lines)
    if extra_tables:
        table_blocks = list(table_blocks) + list(extra_tables)

    for line in lines:
        if not line.text.strip():
            continue
        text = normalize(line.text)
        t_money, t_birth, t_pii = line_table_flags(line, table_blocks)

        for cat, rid, rx, val, grp in compiled:
            for m in rx.finditer(text):
                if val and not validate(val, m.group()):
                    continue
                if rid == "gov_body" and re.search(r"Наименование\s+$", text[: m.start()]):
                    continue
                a, b = (m.start(grp), m.end(grp)) if grp else (m.start(), m.end())
                if a < 0 or b < 0:
                    a, b = m.start(), m.end()
                for r in line.rects_for(a, b):
                    rects.append((r, cat))
                stats[cat] = stats.get(cat, 0) + 1

        if "person" in enabled and only_ids is None:
            person_spans = find_names(line) + english_names(line)
            for a, b in person_spans:
                for r in line.rects_for(a, b):
                    rects.append((r, "person"))
                stats["person"] = stats.get("person", 0) + 1
            # рядом с ФИО дата часто является датой рождения
            if person_spans and "birth" in enabled:
                for m in re.finditer(r"\b\d{2}[.\-]\d{2}[.\-](?:19|20)\d{2}\b", text):
                    for r in line.rects_for(m.start(), m.end()):
                        rects.append((r, "birth"))
                    stats["birth"] = stats.get("birth", 0) + 1

        if aggressive_finance and "finance" in enabled and only_ids is None:
            for w in line.words:
                tok = w.text.strip()
                if is_sensitive_number(
                    tok,
                    text,
                    table_money=t_money,
                    table_birth=t_birth,
                    table_pii=t_pii,
                ):
                    rects.append((w.rect, "finance"))
                    stats["finance"] = stats.get("finance", 0) + 1
            # даты в birth/pii-таблице
            if (t_birth or t_pii) and "birth" in enabled:
                for m in re.finditer(
                    r"\b\d{1,2}[./\-]\d{1,2}[./\-](?:19|20)\d{2}\b", text
                ):
                    for r in line.rects_for(m.start(), m.end()):
                        rects.append((r, "birth"))
                    stats["birth"] = stats.get("birth", 0) + 1


    # После «адрес:» следующие 1–3 строки — адрес, пока не кончится скобка/точка с запятой
    if "address" in enabled and only_ids is None:
        for i, line in enumerate(lines):
            if not re.search(r"адрес\s*:", line.text, re.I):
                continue
            # «Россия» и хвост адреса на той же строке
            m = re.search(r"адрес\s*:\s*(.*)$", line.text, re.I)
            if m and m.group(1).strip():
                for r in line.rects_for(m.start(1), m.end(1)):
                    rects.append((r, "address"))
            for nxt in lines[i + 1 : i + 12]:
                tx = nxt.text.strip()
                if not tx:
                    break
                # новое поле («Директор:», «Телефон:») — адрес закончился
                if re.match(r"^[А-ЯЁA-Z][А-Яа-яЁёA-Za-z\- ]{0,30}:", tx):
                    break
                if re.match(r"^(?:о взыскан|руководств|решил)", tx, re.I):
                    break
                if re.match(r"^[кК]\s+ОБЩЕСТВ", tx) or re.match(r"^ОБЩЕСТВ", tx):
                    break
                for w in nxt.words:
                    rects.append((w.rect, "address"))
                stats["address"] = stats.get("address", 0) + 1
                if ");" in tx:
                    break

    # ФИО, разорванное на две строки: фамилия в конце, имя+отчество в начале следующей
    if "person" in enabled and only_ids is None:
        for i in range(len(lines) - 1):
            l1, l2 = lines[i], lines[i + 1]
            if not l1.words or len(l2.words) < 2:
                continue
            last = _cyr(l1.words[-1].text.strip(".,;:"))
            second = _cyr(l2.words[1].text.strip(".,;:"))
            if SURNAME_STRICT.search(last.lower()) and PATRONYMIC.search(second.lower()):
                for r in (l1.words[-1].rect, l2.words[0].rect, l2.words[1].rect):
                    rects.append((r, "person"))
                stats["person"] = stats.get("person", 0) + 1

    # подпись без значения — значение на следующей строке
    labels = (
        ("reg_number", r"(?:регистрационн|реестров|регистров)\w*\s+номер"),
        ("issue_date", r"(?:дата\s+выдачи|выдан)\w*"),
        (
            "identity",
            r"(?:кем\s+выдан|место\s+рождения|место\s+жительства|серия|"
            r"снилс|страховой\s+номер|полис|код\s+подразделения)",
        ),
    )
    for i, line in enumerate(lines[:-1]):
        for rid, pat in labels:
            if not re.search(pat, line.text, re.I):
                continue
            cat = "docno" if rid == "reg_number" else "identity"
            if cat not in enabled:
                continue
            if re.search(r"\d", line.text):
                continue
            for nxt in lines[i + 1 : i + 3]:
                if not nxt.text.strip():
                    continue
                if rid == "issue_date":
                    for w in nxt.words:
                        rects.append((w.rect, "identity"))
                else:
                    m = re.search(r"\d[\d\s\-/]{0,30}", nxt.text)
                    if not m:
                        continue
                    for r in nxt.rects_for(m.start(), m.end()):
                        rects.append((r, cat))
                stats[cat] = stats.get(cat, 0) + 1
                break

    return rects, stats


# Обратная совместимость: старые имена
def collect_hits(
    lines: List[Line],
    enabled: set,
    mode: str,
    only_ids: Optional[set] = None,
    page_rect: Optional[Rect] = None,
):
    """Обёртка: текстовый путь (без aggressive_finance)."""
    del page_rect
    return find_hits(lines, enabled, mode, only_ids=only_ids, aggressive_finance=False)


def pdf_hits(
    lines: List[Line], enabled: set, mode: str
) -> List[Tuple[Rect, str]]:
    """Обёртка: PDF-путь (с aggressive_finance). Возвращает только rects."""
    rects, _stats = find_hits(lines, enabled, mode, aggressive_finance=True)
    return rects
