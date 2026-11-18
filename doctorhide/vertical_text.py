# -*- coding: utf-8 -*-
"""Детект и склейка вертикально ориентированного текста в PDF.

Признаки вертикального слова/глифа:
  - bbox «высокий и узкий» (height/width ≥ ratio);
  - либо одиночный символ в узком столбце.

Цепочки с близким центром x и монотонным y склеиваются в один Word
(текст сверху вниз или снизу вверх — выбираем более «словарный» вариант).
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from .text_model import Rect, Word


def is_vertical_rect(
    rect: Rect,
    *,
    min_ratio: float = 1.35,
    max_width: float = 22.0,
) -> bool:
    """True, если bbox похож на вертикальный глиф/короткое слово."""
    w = max(rect.width, 0.01)
    h = max(rect.height, 0.01)
    if w > max_width and h / w < min_ratio:
        return False
    return h / w >= min_ratio


def is_vertical_word(word: Word, **kwargs) -> bool:
    return is_vertical_rect(word.rect, **kwargs)


def _cx(w: Word) -> float:
    return (w.rect.x0 + w.rect.x1) / 2


def _cy(w: Word) -> float:
    return (w.rect.y0 + w.rect.y1) / 2


def _union_rect(words: Sequence[Word]) -> Rect:
    return Rect(
        min(w.rect.x0 for w in words),
        min(w.rect.y0 for w in words),
        max(w.rect.x1 for w in words),
        max(w.rect.y1 for w in words),
    )


def _score_text(s: str) -> int:
    """Грубая оценка «похоже на слово/число», а не на мусор."""
    if not s:
        return 0
    score = 0
    letters = sum(1 for c in s if c.isalpha())
    digits = sum(1 for c in s if c.isdigit())
    score += letters * 2 + digits * 3
    # кириллица / латиница подряд
    if letters >= 2:
        score += 2
    return score


def _merge_chain(chain: List[Word]) -> Word:
    """Склеивает цепочку: пробует порядок top→bottom и bottom→top."""
    by_y = sorted(chain, key=_cy)
    t_down = "".join(w.text for w in by_y)
    t_up = "".join(w.text for w in reversed(by_y))
    # с пробелом, если куски длиннее 1 символа
    if any(len(w.text) > 1 for w in by_y):
        t_down_sp = " ".join(w.text for w in by_y)
        t_up_sp = " ".join(w.text for w in reversed(by_y))
        candidates = [t_down, t_up, t_down_sp, t_up_sp]
    else:
        candidates = [t_down, t_up]
    best = max(candidates, key=_score_text)
    return Word(best, _union_rect(by_y))


def merge_vertical_words(
    words: List[Word],
    *,
    x_tol: float = 8.0,
    y_gap_max: float = 16.0,
    min_chain: int = 2,
    min_ratio: float = 1.35,
) -> Tuple[List[Word], List[Word]]:
    """Делит слова на вертикальные цепочки и оставшиеся горизонтальные.

    Возвращает (merged_vertical, horizontal_rest).
    """
    if not words:
        return [], []

    vertical: List[Word] = []
    horizontal: List[Word] = []
    for w in words:
        # одиночные символы в относительно узком боксе тоже кандидаты
        narrow = w.rect.width <= 14.0 and len(w.text.strip()) <= 2
        if is_vertical_word(w, min_ratio=min_ratio) or narrow:
            vertical.append(w)
        else:
            horizontal.append(w)

    if len(vertical) < min_chain:
        return [], words

    # сортировка: сначала x, потом y
    vertical.sort(key=lambda w: (_cx(w), _cy(w)))

    used = [False] * len(vertical)
    merged: List[Word] = []

    for i, w in enumerate(vertical):
        if used[i]:
            continue
        chain = [w]
        used[i] = True
        last = w
        # жадно наращиваем вниз/вверх по y при близком x
        changed = True
        while changed:
            changed = False
            for j, cand in enumerate(vertical):
                if used[j]:
                    continue
                if abs(_cx(cand) - _cx(last)) > x_tol:
                    continue
                gap = abs(_cy(cand) - _cy(last))
                # зазор между центрами с учётом высоты
                max_gap = y_gap_max + 0.5 * (last.rect.height + cand.rect.height)
                if gap > max_gap:
                    continue
                # не прыгаем в сторону по x сильнее
                used[j] = True
                chain.append(cand)
                last = cand
                changed = True
        if len(chain) >= min_chain:
            merged.append(_merge_chain(chain))
        else:
            # вернуть в горизонтальный поток
            horizontal.extend(chain)

    return merged, horizontal


def regroup_words_with_vertical(
    words: List[Word],
    **kwargs,
) -> List[Word]:
    """Исходный список слов → горизонтальные + склеенные вертикальные."""
    merged, rest = merge_vertical_words(words, **kwargs)
    return rest + merged
