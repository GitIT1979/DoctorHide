# -*- coding: utf-8 -*-
"""Тесты детекта и склейки вертикального текста (vertical_text)."""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from doctorhide.text_model import Rect, Word
from doctorhide.vertical_text import (
    _merge_chain,
    _score_text,
    _union_rect,
    is_vertical_rect,
    is_vertical_word,
    merge_vertical_words,
    regroup_words_with_vertical,
)


def V(text: str, x0: float, y0: float, w: float = 8.0, h: float = 12.0) -> Word:
    """Вертикально-подобный глиф (узкий высокий бокс)."""
    return Word(text, Rect(x0, y0, x0 + w, y0 + h))


def H(text: str, x0: float, y0: float, w: float = 40.0, h: float = 10.0) -> Word:
    """Обычное горизонтальное слово."""
    return Word(text, Rect(x0, y0, x0 + w, y0 + h))


class IsVerticalRectTests(unittest.TestCase):
    def test_tall_narrow_is_vertical(self):
        self.assertTrue(is_vertical_rect(Rect(0, 0, 8, 40)))

    def test_wide_short_is_horizontal(self):
        self.assertFalse(is_vertical_rect(Rect(0, 0, 50, 10)))

    def test_square_not_vertical_by_default(self):
        self.assertFalse(is_vertical_rect(Rect(0, 0, 12, 12)))

    def test_custom_min_ratio(self):
        r = Rect(0, 0, 10, 12)  # ratio 1.2
        self.assertFalse(is_vertical_rect(r, min_ratio=1.35))
        self.assertTrue(is_vertical_rect(r, min_ratio=1.1))

    def test_wide_but_very_tall_still_vertical(self):
        # width > max_width(22), но ratio высокий → True
        self.assertTrue(is_vertical_rect(Rect(0, 0, 30, 60), min_ratio=1.35))

    def test_wide_and_flat_rejected(self):
        self.assertFalse(is_vertical_rect(Rect(0, 0, 30, 20), min_ratio=1.35))

    def test_zero_size_safe(self):
        # width/height 0 → max(..., 0.01), не падаем
        self.assertFalse(is_vertical_rect(Rect(5, 5, 5, 5)))

    def test_is_vertical_word_delegates(self):
        self.assertTrue(is_vertical_word(V("А", 0, 0, 8, 20)))
        self.assertFalse(is_vertical_word(H("Сумма", 0, 0)))


class ScoreAndUnionTests(unittest.TestCase):
    def test_score_prefers_digits(self):
        self.assertGreater(_score_text("7707"), _score_text("ab"))

    def test_score_empty(self):
        self.assertEqual(_score_text(""), 0)

    def test_union_rect(self):
        words = [V("1", 10, 20), V("2", 12, 40, h=10)]
        u = _union_rect(words)
        self.assertEqual(u.x0, 10)
        self.assertEqual(u.y0, 20)
        self.assertGreater(u.y1, 40)
        self.assertGreater(u.x1, 12)


class MergeChainTests(unittest.TestCase):
    def test_top_to_bottom_letters(self):
        chain = [V("И", 100, 10), V("Н", 100, 24), V("Н", 100, 38)]
        m = _merge_chain(chain)
        self.assertEqual(m.text.replace(" ", ""), "ИНН")

    def test_bottom_to_top_order_still_readable(self):
        # передан в обратном порядке — сортировка по y внутри
        chain = [V("Н", 100, 38), V("И", 100, 10), V("Н", 100, 24)]
        m = _merge_chain(chain)
        self.assertEqual(m.text.replace(" ", ""), "ИНН")

    def test_multi_char_chunks_may_use_spaces(self):
        chain = [
            Word("77", Rect(50, 10, 58, 30)),
            Word("07", Rect(50, 32, 58, 52)),
        ]
        m = _merge_chain(chain)
        # либо "7707", либо "77 07" — оба допустимы, цифры на месте
        digits = "".join(c for c in m.text if c.isdigit())
        self.assertEqual(digits, "7707")


class MergeVerticalWordsTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(merge_vertical_words([]), ([], []))

    def test_single_word_not_merged(self):
        w = V("А", 0, 0)
        merged, rest = merge_vertical_words([w], min_chain=2)
        self.assertEqual(merged, [])
        self.assertEqual(len(rest), 1)

    def test_horizontal_only_untouched(self):
        words = [H("Иванов", 10, 10), H("Пётр", 60, 10)]
        merged, rest = merge_vertical_words(words)
        self.assertEqual(merged, [])
        self.assertEqual(len(rest), 2)

    def test_vertical_inn_digits(self):
        digits = list("7707083893")
        words = []
        y = 10.0
        for d in digits:
            words.append(V(d, 50, y, w=8, h=12))
            y += 14
        merged, rest = merge_vertical_words(words, min_ratio=1.2, min_chain=3)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].text.replace(" ", ""), "7707083893")
        # bbox покрывает весь столбик
        self.assertAlmostEqual(merged[0].rect.y0, 10, delta=1)
        self.assertGreater(merged[0].rect.y1, 10 + 13 * 9)

    def test_two_separate_columns(self):
        # два столбика на разном x → две цепочки
        left = [V(c, 40, 10 + i * 14) for i, c in enumerate("ИНН")]
        right = [V(c, 200, 10 + i * 14) for i, c in enumerate("123")]
        merged, _ = merge_vertical_words(left + right, min_ratio=1.2, min_chain=2)
        texts = {m.text.replace(" ", "") for m in merged}
        self.assertIn("ИНН", texts)
        self.assertIn("123", texts)

    def test_gap_too_large_breaks_chain(self):
        words = [
            V("1", 50, 10),
            V("2", 50, 24),
            V("3", 50, 200),  # далеко по y
        ]
        merged, rest = merge_vertical_words(
            words, min_ratio=1.2, min_chain=2, y_gap_max=10
        )
        # 1-2 склеены; 3 отдельно (в rest или одиночная цепочка)
        joined = [m.text.replace(" ", "") for m in merged]
        self.assertTrue(any(j == "12" for j in joined) or "12" in "".join(joined))
        all_digits = "".join(m.text for m in merged) + "".join(w.text for w in rest)
        self.assertIn("3", all_digits)

    def test_x_misalignment_not_merged(self):
        words = [
            V("A", 50, 10),
            V("B", 80, 24),  # другой x
        ]
        merged, rest = merge_vertical_words(
            words, min_ratio=1.2, min_chain=2, x_tol=5
        )
        self.assertEqual(merged, [])
        self.assertEqual(len(rest), 2)

    def test_mixed_horizontal_and_vertical(self):
        words = [
            H("Сумма", 10, 10),
            V("1", 200, 10),
            V("2", 200, 24),
            V("3", 200, 38),
            H("руб", 10, 50),
        ]
        merged, rest = merge_vertical_words(words, min_ratio=1.2, min_chain=2)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].text.replace(" ", ""), "123")
        horiz_texts = {w.text for w in rest}
        self.assertIn("Сумма", horiz_texts)
        self.assertIn("руб", horiz_texts)

    def test_narrow_single_chars_without_tall_ratio(self):
        # width<=14, 1 символ → кандидат даже при ratio < 1.35
        words = [
            Word("7", Rect(10, 10, 18, 18)),  # 8x8, ratio 1
            Word("7", Rect(10, 20, 18, 28)),
            Word("0", Rect(10, 30, 18, 38)),
        ]
        merged, _ = merge_vertical_words(words, min_chain=2)
        self.assertTrue(merged)
        self.assertEqual(merged[0].text.replace(" ", ""), "770")

    def test_regroup_combines(self):
        words = [H("A", 0, 0), V("1", 100, 0), V("2", 100, 14)]
        out = regroup_words_with_vertical(words, min_ratio=1.2, min_chain=2)
        texts = [w.text.replace(" ", "") for w in out]
        self.assertIn("A", texts)
        self.assertTrue(any("12" in t or t == "12" for t in texts))


class FindHitsIntegrationTests(unittest.TestCase):
    """Склейка vertical_text → Line → find_hits (как в PDF-пайплайне)."""

    @staticmethod
    def _stack(chars, x=50.0, y0=10.0, step=14.0, w=8.0, h=12.0):
        words = []
        y = y0
        for ch in chars:
            words.append(V(ch, x, y, w=w, h=h))
            y += step
        return words

    @staticmethod
    def _merge_lines(raw_words, **kwargs):
        from doctorhide.text_model import _group_lines
        from doctorhide.vertical_text import merge_vertical_words

        kw = dict(min_ratio=1.2, min_chain=2)
        kw.update(kwargs)
        merged, rest = merge_vertical_words(raw_words, **kw)
        lines = []
        if merged:
            
            for m in merged:
                lines.extend(_group_lines([m]))
        if rest:
            # горизонтальный хвост — по одной линии
            for w in rest:
                lines.extend(_group_lines([w]))
        return merged, lines

    def test_merged_inn_detected(self):
        from doctorhide.categories import CATEGORIES
        from doctorhide.detect import find_hits

        raw = self._stack(list("7707083893"))
        merged, lines = self._merge_lines(raw, min_chain=3)
        self.assertTrue(merged)
        hits, stats = find_hits(lines, set(CATEGORIES), aggressive_finance=False)
        self.assertGreaterEqual(stats.get("codes", 0), 1, msg=f"{hits=} {stats=}")
        self.assertTrue(any(c == "codes" for _, c in hits))

    def test_unmerged_single_digits_not_full_inn(self):
        """Без склейки отдельные цифры не дают целый ИНН."""
        from doctorhide.categories import CATEGORIES
        from doctorhide.detect import find_hits
        from doctorhide.text_model import _group_lines

        raw = self._stack(list("7707083893"))
        # каждая цифра — отдельная Line (как до merge)
        lines = []
        for w in raw:
            lines.extend(_group_lines([w]))
        hits, stats = find_hits(lines, set(CATEGORIES), aggressive_finance=False)
        # отдельная цифра "7" не валидный inn10/12
        self.assertEqual(stats.get("codes", 0), 0, msg=f"{hits=} {stats=}")

    def test_merged_snils_like_digits(self):
        """Склеенный 11-значный фрагмент (СНИЛС без дефисов) — policy16/числа.

        Полный СНИЛС с дефисами в вертикали редок; проверяем, что длинная
        цифра после merge вообще доходит до rules (codes/finance path).
        """
        from doctorhide.categories import CATEGORIES
        from doctorhide.detect import find_hits

        # 16 цифр — policy16 в codes
        raw = self._stack(list("1234567890123456"))
        merged, lines = self._merge_lines(raw, min_chain=3)
        self.assertTrue(merged)
        hits, stats = find_hits(lines, set(CATEGORIES))
        self.assertTrue(
            stats.get("codes", 0) >= 1 or any(c == "codes" for _, c in hits),
            msg=f"{hits=} {stats=}",
        )

    def test_merged_token_bbox_covers_stack(self):
        from doctorhide.categories import CATEGORIES
        from doctorhide.detect import find_hits

        raw = self._stack(list("7707083893"), y0=10, step=14)
        merged, lines = self._merge_lines(raw, min_chain=3)
        hits, stats = find_hits(lines, set(CATEGORIES))
        code_rects = [r for r, c in hits if c == "codes"]
        self.assertTrue(code_rects, stats)
        r = code_rects[0]
        self.assertLessEqual(r.y0, 12)
        self.assertGreaterEqual(r.y1, 10 + 14 * 9)

    def test_codes_disabled_no_inn_hit(self):
        from doctorhide.categories import CATEGORIES
        from doctorhide.detect import find_hits

        raw = self._stack(list("7707083893"))
        _, lines = self._merge_lines(raw, min_chain=3)
        enabled = set(CATEGORIES) - {"codes"}
        hits, stats = find_hits(lines, enabled)
        self.assertEqual(stats.get("codes", 0), 0)
        self.assertFalse(any(c == "codes" for _, c in hits))

    def test_vertical_phone_digits(self):
        from doctorhide.categories import CATEGORIES
        from doctorhide.detect import find_hits

        # phone rule: (?:+7|8)... — вертикальный номер с ведущей 8
        raw = self._stack(list("89161234567"))
        merged, lines = self._merge_lines(raw, min_chain=3)
        self.assertEqual(merged[0].text.replace(" ", ""), "89161234567")
        hits, stats = find_hits(lines, set(CATEGORIES))
        self.assertTrue(
            stats.get("contacts", 0) >= 1 or any(c == "contacts" for _, c in hits),
            msg=f"expected contacts hit, got {stats=} {hits=}",
        )

    def test_aggressive_finance_on_merged_sum(self):
        from doctorhide.categories import CATEGORIES
        from doctorhide.detect import find_hits
        from doctorhide.text_model import _group_lines

        # вертикальная «сумма» 150000 + горизонтальный якорь «руб» на той же линии-контексте
        raw = self._stack(list("150000"), x=80)
        merged, _ = self._merge_lines(raw, min_chain=2)
        # линия: руб + склеенное число (контекст money в text)
        from doctorhide.text_model import Line, Word, Rect

        rub = H("руб", 10, 10)
        num = merged[0]
        # одна Line с двумя words
        text = "руб " + num.text
        starts = [0, 4]
        line = Line(text, [rub, num], starts)
        hits, stats = find_hits(
            [line], set(CATEGORIES), aggressive_finance=True
        )
        self.assertGreaterEqual(
            stats.get("finance", 0), 1,
            msg=f"expected finance hit, {stats=} {hits=}",
        )

    def test_two_vertical_columns_both_detected(self):
        from doctorhide.categories import CATEGORIES
        from doctorhide.detect import find_hits

        left = self._stack(list("7707083893"), x=40)
        right = self._stack(list("1234567890123456"), x=200)
        merged, lines = self._merge_lines(left + right, min_chain=3)
        self.assertGreaterEqual(len(merged), 2)
        hits, stats = find_hits(lines, set(CATEGORIES))
        self.assertGreaterEqual(stats.get("codes", 0), 1, msg=stats)

    def test_horizontal_fio_still_works_beside_vertical(self):
        """Горизонтальное ФИО не ломается соседним вертикальным столбиком."""
        from doctorhide.categories import CATEGORIES
        from doctorhide.detect import find_hits
        from doctorhide.text_model import Line, Word, Rect, _group_lines

        fio_words = [
            Word("Иванов", Rect(10, 100, 60, 110)),
            Word("Иван", Rect(65, 100, 95, 110)),
            Word("Иванович", Rect(100, 100, 160, 110)),
        ]
        fio_line = _group_lines(fio_words)[0]
        vert = self._stack(list("7707083893"), x=300, y0=10)
        merged, vlines = self._merge_lines(vert, min_chain=3)
        hits, stats = find_hits([fio_line] + vlines, set(CATEGORIES))
        self.assertGreaterEqual(stats.get("person", 0), 1, msg=stats)
        self.assertGreaterEqual(stats.get("codes", 0), 1, msg=stats)


if __name__ == "__main__":
    unittest.main()
