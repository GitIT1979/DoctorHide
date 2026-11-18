# -*- coding: utf-8 -*-
"""Обезличивание plain-текста: замена значений на placeholder."""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from . import config
from .finance import is_finance_token, mask_numbers  # re-export
from .names import english_names, find_names, normalize
from .rules import RULES, validate
from .text_model import lines_with_offsets
from .engine import replace_spans

# Precompiled rules (same order key as before)
def _rule_order(rule):
    cat, rid, pat, val, grp = rule
    if rid.startswith("court"):
        priority = 0
    elif rid.startswith("institution"):
        priority = 1
    else:
        priority = 2
    return (priority, -len(pat))


_COMPILED: List[Tuple[str, str, re.Pattern, Optional[str], int]] = [
    (cat, rid, re.compile(pat, re.I), val, grp)
    for cat, rid, pat, val, grp in sorted(RULES, key=_rule_order)
]


def anonymize_text(text: str, mode: str, enabled: set):
    """mode зарезервирован: на набор правил сейчас не влияет (см. default_enabled)."""
    stats: Dict[str, int] = {}
    text = normalize(text)
    del mode
    if "org" in enabled:
        text, n = replace_spans(text, config.PLACEHOLDER)
        if n:
            stats["org"] = stats.get("org", 0) + n

    def sub(cat, rid, rx, val, grp):
        nonlocal text

        def repl(m):
            g = m.group()
            # не трогаем уже вставленный placeholder (swift ловил слово censored)
            if g.strip("[]«»").lower() == "censored" or g == config.PLACEHOLDER:
                return g
            if val and not validate(val, g):
                return g
            stats[cat] = stats.get(cat, 0) + 1
            if grp:
                whole = g
                gs = m.start(grp) - m.start(0)
                ge = m.end(grp) - m.start(0)
                if gs >= 0 and ge >= 0:
                    return whole[:gs] + config.PLACEHOLDER + whole[ge:]
            return config.PLACEHOLDER

        text = rx.sub(repl, text)

    for cat, rid, rx, val, grp in _COMPILED:
        if cat not in enabled:
            continue
        if not rx.search(text):
            continue
        sub(cat, rid, rx, val, grp)

    if "person" in enabled:
        spans: List[Tuple[int, int]] = []
        for base, ln in lines_with_offsets(text):
            spans.extend((base + a, base + b) for a, b in find_names(ln) + english_names(ln))
        spans.sort()
        merged: List[Tuple[int, int]] = []
        for a, b in spans:
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        if merged:
            parts, cur = [], 0
            for a, b in merged:
                parts.append(text[cur:a])
                parts.append(config.PLACEHOLDER)
                cur = b
                stats["person"] = stats.get("person", 0) + 1
            parts.append(text[cur:])
            text = "".join(parts)

    # finance category: sentence-aware number masking (не счета — они в bank)
    if "finance" in enabled:
        text = mask_numbers(text, stats)
    return text, stats


__all__ = ["anonymize_text", "mask_numbers", "is_finance_token"]
