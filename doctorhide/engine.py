# -*- coding: utf-8 -*-
"""Движок детекции: склейка строк + явные шаблоны.

Решает переносы: суд, ОПФ+имя, адрес ищутся по склеенному тексту,
а плашки ставятся по словам-источникам.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .text_model import Line, Rect

# нормализация кавычек
_QUOTES = str.maketrans({
    "«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "‟": '"',
    "‘": "'", "’": "'",
})

OPF = (
    r"ООО|ОАО|ЗАО|ПАО|АО|ИП|ФГУП|ГУП|МУП|АНО|НКО|"
    r"LLC|Ltd\.?|Inc\.?|GmbH|Corporation|Corp\.?|PLC|AG|SA|BV|NV"
)
OPF_FULL = (
    r"обществ\w*\s+с\s+ограниченн\w*\s+ответственност\w*|"
    r"акционерн\w*\s+обществ\w*"
)

# имя юрлица: в кавычках или 1–4 слова с заглавной после ОПФ
NAME_Q = r'"([^"\n]{1,80})"'
NAME_PLAIN = (
    r"([А-ЯЁA-Z][А-ЯЁA-Za-zа-яё\-]{1,40}"
    r"(?:\s+[А-ЯЁA-Za-zа-яё\-]{1,40}){0,3})"
)

PATTERNS: List[Tuple[str, str, int]] = [
    # суд целиком, через переносы
    ("org",
     r"(?:[А-ЯЁа-яё\-]+\s+){0,4}"
     r"(?:арбитражн\w+|верховн\w+|конституционн\w+|кассационн\w+|"
     r"апелляционн\w+|районн\w+|городск\w+|областн\w+|миров\w+)\s+"
     r"суд\w*(?:\s+[А-ЯЁа-яё\-]+){0,8}",
     0),
    ("org", r"\bАС\s+[А-ЯЁ][А-Яа-яЁё\-\s]{2,40}", 0),
    # e-mail, разорванный переносом строки: «ivanov@» / «example.ru»
    ("contacts", r"[\w.+-]+@\s*\n\s*[\w-]{2,}(?:\.[\w-]{2,})+", 0),
    # ОПФ + имя в кавычках (скрываем только имя)
    ("org", rf"\b(?:{OPF})\s*{NAME_Q}", 1),
    ("org", rf"(?:{OPF_FULL})\s*{NAME_Q}", 1),
    # ОПФ + имя без кавычек
    ("org", rf"\b(?:{OPF})\s+{NAME_PLAIN}", 1),
    ("org", rf"(?:{OPF_FULL})\s+{NAME_PLAIN}", 1),
    # хвост ОПФ + имя (после переноса)
    ("org", rf"(?:ответственност\w*|ограниченн\w*)\s+{NAME_PLAIN}", 1),
    ("org", r'ответственност\w*\s*"([^"]{1,60})"', 1),
    ("org", r'(?:обществ\w*|ооо|ао|пао|зао)\s*"([^"]{1,60})"', 1),
    ("org", rf"\bобществ\w*\s+{NAME_PLAIN}", 1),
    # имя перед ОПФ: Alpha Ribbon GmbH
    ("org",
     r"\b([A-Za-zА-ЯЁа-яё][A-Za-zА-ЯЁа-яё0-9\s\-]{1,50}?)\s+"
     r"(?:LLC|Ltd\.?|Inc\.?|GmbH|Corp\.?|PLC|AG)\b",
     1),
]


_COMPILED_PATTERNS = [
    (cat, re.compile(pat, re.I), grp) for cat, pat, grp in PATTERNS
]
_OPF_STOP = re.compile(r"(?:ответственност\w*|ограниченн\w*|обществ\w*)", re.I)


def _norm(s: str) -> str:
    return s.translate(_QUOTES)


class _Map:
    """Склеенный текст и обратное отображение позиций на слова."""

    def __init__(self, lines: List[Line]):
        self.lines = lines
        parts: List[str] = []
        # (start_in_joined, line_idx, word_idx)
        self.words: List[Tuple[int, int, int]] = []
        pos = 0
        for li, ln in enumerate(lines):
            if parts:
                parts.append("\n")
                pos += 1
            line_norm = _norm(ln.text)
            # пересчитать starts по нормализованной строке той же длины
            # кавычки 1:1, длина не меняется
            base = pos
            parts.append(line_norm)
            for wi, (w, st) in enumerate(zip(ln.words, ln.starts)):
                self.words.append((base + st, li, wi))
            pos += len(line_norm)
        self.text = "".join(parts)

    def rects_for(self, a: int, b: int) -> List[Rect]:
        out: List[Rect] = []
        for start, li, wi in self.words:
            w = self.lines[li].words[wi]
            end = start + len(w.text)
            if start < b and end > a:
                out.append(w.rect)
        return out


def find_spans(lines: List[Line]) -> List[Tuple[Rect, str]]:
    """Явные шаблоны по склеенному тексту. Возвращает (rect, category)."""
    if not lines:
        return []
    mp = _Map(lines)
    hits: List[Tuple[Rect, str]] = []
    seen = set()
    for cat, rx, grp in _COMPILED_PATTERNS:
        for m in rx.finditer(mp.text):
            if grp and m.lastindex and m.lastindex >= grp:
                a, b = m.start(grp), m.end(grp)
            else:
                a, b = m.start(), m.end()
            # не скрываем сами слова ОПФ, если группа пустая
            frag = mp.text[a:b].strip(' "')
            if _OPF_STOP.fullmatch(frag):
                continue
            key = (a, b, cat)
            if key in seen:
                continue
            seen.add(key)
            for r in mp.rects_for(a, b):
                hits.append((r, cat))
    return hits


def replace_spans(text: str, placeholder: str, enabled: Optional[set] = None) -> tuple:
    """Заменяет найденное в тексте. Возвращает (новый текст, счётчики по категориям).

    enabled=None — все категории. Счётчики ведутся по категориям, а не одной
    «org»: в этом проходе есть и e-mail через перенос строки.
    """
    from .text_model import lines_from_text
    lines = lines_from_text(text)
    if not lines:
        return text, {}
    mp = _Map(lines)
    spans = []
    seen = set()
    for cat, rx, grp in _COMPILED_PATTERNS:
        if enabled is not None and cat not in enabled:
            continue
        for m in rx.finditer(mp.text):
            a, b = (m.start(grp), m.end(grp)) if grp else (m.start(), m.end())
            frag = mp.text[a:b].strip(' "')
            if _OPF_STOP.fullmatch(frag):
                continue
            if (a, b) in seen:
                continue
            seen.add((a, b))
            spans.append((a, b, cat))
    if not spans:
        return text, {}
    spans.sort()
    parts, cur, stats = [], 0, {}
    for a, b, cat in spans:
        if a < cur:
            continue
        parts.append(mp.text[cur:a])
        parts.append(placeholder)
        cur = b
        stats[cat] = stats.get(cat, 0) + 1
    parts.append(mp.text[cur:])
    return "".join(parts), stats
