# -*- coding: utf-8 -*-
"""Эвристика таблиц PDF по x- и y-координатам.

x — кластеры колонок (x0 слов).
y — кластеры строк (центр по вертикали) и вертикальные пары
    «подпись сверху → значение снизу» в той же колонке (формы/анкеты).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .finance import _BIRTH_AGE, _CURRENCY_ONLY, _MONEY, _TABLE_PII_FLAG, NUM_RX
from .text_model import Line, Rect, Word


@dataclass
class TableBlock:
    """Табличный или форм-блок: строки и флаги содержимого."""

    lines: List[Line] = field(default_factory=list)
    money: bool = False
    birth: bool = False
    pii: bool = False
    line_ids: Set[int] = field(default_factory=set)

    def contains_line(self, line: Line) -> bool:
        return id(line) in self.line_ids


def _cluster_values(vals: List[float], tol: float) -> List[float]:
    if not vals:
        return []
    vals = sorted(vals)
    clusters: List[List[float]] = [[vals[0]]]
    for v in vals[1:]:
        if abs(v - clusters[-1][-1]) <= tol:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return [sum(c) / len(c) for c in clusters]


def _assign_band(v: float, centers: List[float], tol: float) -> Optional[int]:
    best, bi = None, None
    for i, c in enumerate(centers):
        d = abs(v - c)
        if d <= tol and (best is None or d < best):
            best, bi = d, i
    return bi


def _row_y_key(line: Line) -> float:
    if not line.words:
        return 0.0
    ys = [(w.rect.y0 + w.rect.y1) / 2 for w in line.words]
    return sum(ys) / len(ys)


def _has_bbox(line: Line) -> bool:
    return bool(line.words) and not all(
        w.rect.width == 0 and w.rect.height == 0 for w in line.words
    )


def _set_flags_from_blob(tb: TableBlock, lines: List[Line]) -> None:
    blob = "\n".join(ln.text for ln in lines)
    tb.money = bool(_MONEY.search(blob)) or any(
        _CURRENCY_ONLY.match(w.text.strip()) for ln in lines for w in ln.words
    )
    if not tb.money:
        for ln in lines:
            if NUM_RX.search(ln.text) and _MONEY.search(ln.text):
                tb.money = True
                break
    tb.birth = bool(_BIRTH_AGE.search(blob))
    tb.pii = bool(_TABLE_PII_FLAG.search(blob))


def _cluster_lines_by_y(
    lines: List[Line], y_tol: float
) -> List[Tuple[float, List[Line]]]:
    """Перегруппировка строк по y-центрам (на случай размазанных line-объектов)."""
    items = []
    for ln in lines:
        if not _has_bbox(ln):
            continue
        items.append((_row_y_key(ln), ln))
    if not items:
        return []
    items.sort(key=lambda t: t[0])
    bands: List[Tuple[float, List[Line]]] = []
    cur_ys = [items[0][0]]
    cur_lns = [items[0][1]]
    for y, ln in items[1:]:
        if abs(y - cur_ys[-1]) <= y_tol:
            cur_ys.append(y)
            cur_lns.append(ln)
        else:
            bands.append((sum(cur_ys) / len(cur_ys), cur_lns))
            cur_ys, cur_lns = [y], [ln]
    bands.append((sum(cur_ys) / len(cur_ys), cur_lns))
    return bands


def detect_table_blocks(
    lines: List[Line],
    *,
    min_cols: int = 2,
    min_rows: int = 2,
    x_tol: float = 12.0,
    y_tol: float = 6.0,
    y_gap_max: float = 18.0,
) -> List[TableBlock]:
    """Табличные блоки: колонки по x, строки и ритм по y."""
    usable = [ln for ln in lines if _has_bbox(ln)]
    if len(usable) < min_rows:
        # всё равно пробуем вертикальные пары форм
        return _detect_form_blocks(usable, x_tol=x_tol, y_gap_max=y_gap_max)

    # y-полосы (строки таблицы)
    y_bands = _cluster_lines_by_y(usable, y_tol)
    if len(y_bands) < min_rows:
        return _detect_form_blocks(usable, x_tol=x_tol, y_gap_max=y_gap_max)

    # x-колонки по всем словам
    all_x = [w.rect.x0 for ln in usable for w in ln.words]
    centers = _cluster_values(all_x, x_tol)
    if len(centers) < min_cols:
        return _detect_form_blocks(usable, x_tol=x_tol, y_gap_max=y_gap_max)

    def band_cols(band_lines: List[Line]) -> Set[int]:
        cols: Set[int] = set()
        for ln in band_lines:
            for w in ln.words:
                ci = _assign_band(w.rect.x0, centers, x_tol)
                if ci is not None:
                    cols.add(ci)
        return cols

    # одна «логическая строка» на y-полосу (склейка Line для id-флагов)
    row_meta: List[Tuple[List[Line], Set[int], float]] = []
    for y, band_lines in y_bands:
        cols = band_cols(band_lines)
        row_meta.append((band_lines, cols, y))

    blocks: List[TableBlock] = []
    i = 0
    while i < len(row_meta):
        band_lines, cols, y = row_meta[i]
        if len(cols) < min_cols:
            i += 1
            continue
        group = [row_meta[i]]
        j = i + 1
        gaps: List[float] = []
        while j < len(row_meta):
            bl2, cols2, y2 = row_meta[j]
            if len(cols2) < min_cols:
                break
            prev_y = group[-1][2]
            gap = y2 - prev_y
            if gap > y_gap_max * 2.5:
                break
            # ритм по y: если уже есть зазоры, новый не должен сильно выбиваться
            if gaps and gap > max(gaps) * 2.2 + y_tol:
                break
            if len(cols2 & group[0][1]) < max(1, min_cols - 1):
                break
            gaps.append(gap)
            group.append(row_meta[j])
            j += 1
        if len(group) >= min_rows:
            tb = TableBlock()
            flat: List[Line] = []
            for bl, _, _ in group:
                for ln in bl:
                    tb.lines.append(ln)
                    tb.line_ids.add(id(ln))
                    flat.append(ln)
            _set_flags_from_blob(tb, flat)
            blocks.append(tb)
            i = j
        else:
            i += 1

    # вертикальные формы (подпись над значением) — дополняют блоки
    form_blocks = _detect_form_blocks(usable, x_tol=x_tol, y_gap_max=y_gap_max)
    blocks.extend(form_blocks)
    return blocks


def _detect_form_blocks(
    lines: List[Line],
    *,
    x_tol: float = 12.0,
    y_gap_max: float = 22.0,
) -> List[TableBlock]:
    """Вертикальные пары по y: label (верх) + value (низ) ≈ один x.

    Если подпись money/birth/pii — значение на строке ниже в той же колонке
    попадает в блок с соответствующим флагом.
    """
    usable = [ln for ln in lines if _has_bbox(ln)]
    if len(usable) < 2:
        return []
    usable = sorted(usable, key=_row_y_key)

    # слова с флагами на верхней строке → ищем число/дату ниже по y при близком x
    blocks: List[TableBlock] = []
    for i, upper in enumerate(usable):
        u_money = bool(_MONEY.search(upper.text)) or any(
            _CURRENCY_ONLY.match(w.text.strip()) for w in upper.words
        )
        u_birth = bool(_BIRTH_AGE.search(upper.text))
        u_pii = bool(_TABLE_PII_FLAG.search(upper.text))
        if not (u_money or u_birth or u_pii):
            continue
        uy = _row_y_key(upper)
        # опорные x подписей
        label_xs = [(w.rect.x0 + w.rect.x1) / 2 for w in upper.words]
        if not label_xs:
            continue
        linked: List[Line] = [upper]
        for lower in usable[i + 1 :]:
            ly = _row_y_key(lower)
            if ly - uy > y_gap_max * 2:
                break
            if ly <= uy + 1:
                continue
            # есть слово lower с x рядом с label
            hit = False
            for w in lower.words:
                cx = (w.rect.x0 + w.rect.x1) / 2
                if any(abs(cx - lx) <= x_tol * 2.5 for lx in label_xs):
                    hit = True
                    break
            if hit:
                linked.append(lower)
                # одна «ступень» вниз достаточна для пары; можно взять ещё одну
                if len(linked) >= 3:
                    break
        if len(linked) < 2:
            continue
        tb = TableBlock()
        for ln in linked:
            tb.lines.append(ln)
            tb.line_ids.add(id(ln))
        tb.money = u_money
        tb.birth = u_birth
        tb.pii = u_pii
        # нижние строки могут усилить флаги
        _extra = TableBlock()
        _set_flags_from_blob(_extra, linked)
        tb.money = tb.money or _extra.money
        tb.birth = tb.birth or _extra.birth
        tb.pii = tb.pii or _extra.pii
        blocks.append(tb)
    return blocks


def line_table_flags(
    line: Line, blocks: List[TableBlock]
) -> Tuple[bool, bool, bool]:
    """(money, birth, pii) — объединение по всем блокам, содержащим строку."""
    money = birth = pii = False
    for b in blocks:
        if b.contains_line(line):
            money = money or b.money
            birth = birth or b.birth
            pii = pii or b.pii
    return money, birth, pii
