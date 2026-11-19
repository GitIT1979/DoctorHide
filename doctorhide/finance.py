# -*- coding: utf-8 -*-
"""Эвристики финансовых и «чувствительных» чисел + политика таблиц.

Текст: число скрываем при якоре в том же предложении (валюта/сумма, д.р., документ).
Таблицы (Markdown |…|): при любом money/PII red-flag во всей таблице
все подходящие числа/даты в таблице считаются секретными.

Банковские счета (в т.ч. с пробелами, IBAN) — категория bank в rules + нормализация.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from . import config

NUM_RX = re.compile(r"(?<![\w.,])\d[\d\s\u00a0]*(?:[.,]\d{1,3})?(?![\w])")
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")

# --- якоря «деньги / условные единицы» (B) ---
_MONEY = re.compile(
    r"(?:руб\w*|₽|usd|eur|евро|долл\w*|gbp|jpy|cny|chf|try|kzt|byn|uah|"
    r"тыс\.?|млн\w*|млрд\w*|у\.?\s*е\.?|\bуе\b|усл\.?\s*ед\w*|"
    r"экю|\becu\b|\bsdr\b|балл\w*|бонус\w*|крипт\w*|bitcoin|\bbtc\b|"
    r"\beth\w*\b|\busdt\b|\busdc\b|токен\w*|монет\w*|coins?\b|"
    r"сумм\w*|доход\w*|выручк\w*|прибыл\w*|убыт\w*|зарплат\w*|заработн\w*|"
    r"кредит\w*|займ\w*|ипотек\w*|задолжен\w*|долг\w*|налог\w*|пенси\w*|"
    r"пособи\w*|штраф\w*|пени\b|неустойк\w*|оплат\w*|платеж\w*|стоимост\w*|"
    r"цена\b|тариф\w*|аванс\w*|остат\w*|баланс\w*|компенсац\w*|госпошлин\w*|"
    r"депозит\w*|взнос\w*|перечисл\w*|перевод\w*\s+средств|валют\w*)",
    re.I,
)

# валюта без цифр в ячейке (для соседних ячеек таблицы)
_CURRENCY_ONLY = re.compile(
    r"^(?:руб\w*\.?|₽|usd|eur|евро|долл\w*\.?|gbp|jpy|cny|uah|kzt|byn|"
    r"у\.?\s*е\.?|уе|усл\.?\s*ед\w*|экю|ecu|sdr|btc|eth|usdt|%|проц\w*)$",
    re.I,
)

# --- д.р. / возраст (D) ---
_BIRTH_AGE = re.compile(
    r"(?:дата\s+рожден\w*|рожден\w+|возрост\w*|исполнил\w*|"
    r"\bд\s*[./]\s*р\.?\b|\bд/р\b|\bдр\b|\bг\s*[./]\s*р\.?\b|\bг/р\b|"
    r"г\.?\s*р\.?|год\w*\s+рожден\w*|"
    r"\b\d{1,3}\s*(?:полных\s+)?лет\b)",
    re.I,
)

_DOC_ISSUE = re.compile(
    r"(?:паспорт\w*|выдан\w*|выдач\w*|серия|код\s+подразделения|"
    r"водительск\w*|загран\w*|удостоверен\w*|снилс|инн|полис|"
    r"свидетельств\w*|номер\s+документа|№\s*документа)",
    re.I,
)

# Red-flag заголовков/ячеек таблицы → вся таблица «чувствительная»
_TABLE_PII_FLAG = re.compile(
    r"(?:фио|ф\.и\.о|фамили\w*|имя\b|отчеств\w*|паспорт\w*|снилс|инн|"
    r"телефон|e-?mail|почта|адрес|регистрац\w*|рожд\w*|д\s*[./]\s*р|"
    r"д/р|\bдр\b|г\s*[./]\s*р|счет|счёт|карт\w*|банк\w*|сумм\w*|оплат\w*|"
    r"доход\w*|зарплат\w*|долг\w*|кредит\w*|получател\w*|плательщик\w*|"
    r"бенефициар\w*|iban|swift|bic|инн|кпп|огрн)",
    re.I,
)

_FORMATTED_SUM = re.compile(
    r"^\d{1,3}(?:[\s\u00a0]\d{3})+(?:[.,]\d{1,2})?$|^\d+[.,]\d{2}$"
)
_YEAR_RX = re.compile(r"^(?:19|20)\d{2}$")
_DATE_RX = re.compile(r"^\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}$")

# Markdown table line
_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")


def _sentence_spans(text: str) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    start = 0
    for m in _SENT_SPLIT.finditer(text):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(text)))
    return [(a, b) for a, b in spans if b > a]


def _sentence_at(text: str, pos: int, spans: List[Tuple[int, int]]) -> str:
    for a, b in spans:
        if a <= pos < b:
            return text[a:b]
    return text


def _parse_md_tables(text: str) -> List[Tuple[int, int, List[List[str]]]]:
    """Находит markdown-таблицы: (start, end, rows as list of cell strings)."""
    lines = text.split("\n")
    # map line index → char offset
    offsets = []
    pos = 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln) + 1

    tables: List[Tuple[int, int, List[List[str]]]] = []
    i = 0
    while i < len(lines):
        if not _TABLE_LINE.match(lines[i]):
            i += 1
            continue
        start_i = i
        block = []
        while i < len(lines) and _TABLE_LINE.match(lines[i]):
            block.append(lines[i])
            i += 1
        if len(block) < 2:
            continue
        rows: List[List[str]] = []
        for raw in block:
            if _TABLE_SEP.match(raw):
                continue
            cells = [c.strip() for c in raw.strip().strip("|").split("|")]
            rows.append(cells)
        if not rows:
            continue
        start = offsets[start_i]
        end = offsets[i - 1] + len(lines[i - 1])
        tables.append((start, end, rows))
    return tables


def _table_flags(rows: List[List[str]]) -> Dict[str, bool]:
    """Red flags по всей таблице."""
    money = False
    birth = False
    pii = False
    for r in rows:
        for cell in r:
            if not cell:
                continue
            if _MONEY.search(cell) or _CURRENCY_ONLY.match(cell.strip()):
                money = True
            if _BIRTH_AGE.search(cell):
                birth = True
            if _TABLE_PII_FLAG.search(cell):
                pii = True
            # число + валютный якорь в одной ячейке
            if NUM_RX.search(cell) and _MONEY.search(cell):
                money = True
    # соседство: ячейка с только валютой рядом с числом уже покрыто money=True
    return {"money": money, "birth": birth, "pii": pii}


def is_sensitive_number(
    token: str,
    sentence: str,
    *,
    table_money: bool = False,
    table_birth: bool = False,
    table_pii: bool = False,
) -> bool:
    """Нужно ли скрывать число token в контексте sentence / таблицы."""
    tok = token.strip()
    if not tok:
        return False
    digits = sum(ch.isdigit() for ch in tok)
    if digits < 2:
        return False
    if re.search(r"[A-Za-zА-Яа-яЁё]", tok):
        return False

    # В money-таблице — любое число (кроме чистой даты — даты при birth/pii)
    if table_money and not _DATE_RX.match(tok):
        return True

    # Дата в birth/pii-таблице
    if _DATE_RX.match(tok):
        return table_birth or table_pii

    sent = sentence or ""
    money = table_money or bool(_MONEY.search(sent))
    birth = table_birth or bool(_BIRTH_AGE.search(sent))
    doc = bool(_DOC_ISSUE.search(sent))

    if table_pii and digits >= 4 and not _YEAR_RX.match(tok):
        # длинные числа в PII-таблице (кроме голых годов решений)
        return True

    if not (money or birth or doc or table_pii):
        return False

    if _YEAR_RX.match(tok):
        return birth or table_birth

    if _FORMATTED_SUM.match(tok):
        return money or table_money

    if digits <= 3:
        if birth or table_birth:
            return True
        if money or table_money:
            return True
        return False

    return True


def is_finance_token(token: str, context: str = "") -> bool:
    return is_sensitive_number(token, context)


def mask_numbers(text: str, stats: Dict[str, int]) -> str:
    """Маскирует чувствительные числа; для markdown-таблиц — политика «вся таблица»."""
    spans = _sentence_spans(text)
    tables = _parse_md_tables(text)

    def flags_at(pos: int) -> Tuple[bool, bool, bool]:
        for a, b, rows in tables:
            if a <= pos < b:
                f = _table_flags(rows)
                return f["money"], f["birth"], f["pii"]
        return False, False, False

    out, last = [], 0
    for m in NUM_RX.finditer(text):
        token = m.group()
        sent = _sentence_at(text, m.start(), spans)
        tm, tb, tp = flags_at(m.start())
        if not is_sensitive_number(
            token, sent, table_money=tm, table_birth=tb, table_pii=tp
        ):
            continue
        out.append(text[last : m.start()])
        out.append(config.PLACEHOLDER)
        last = m.end()
        stats["finance"] = stats.get("finance", 0) + 1
    out.append(text[last:])
    return "".join(out)
