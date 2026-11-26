# -*- coding: utf-8 -*-
"""Регрессии P1: детекция в PDF и тексте (без OCR)."""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from doctorhide import CATEGORIES  # noqa: E402
from doctorhide.anonymize import anonymize_text  # noqa: E402
from doctorhide.detect import find_hits  # noqa: E402
from doctorhide.names import find_names  # noqa: E402
from doctorhide.text_model import lines_from_text  # noqa: E402


def hits(text: str, **kw):
    lines = lines_from_text(text)
    return find_hits(lines, set(CATEGORIES), "document", **kw)


class LabelTextValueTests(unittest.TestCase):
    """Подпись, значение — текстом на следующей строке."""

    def test_place_of_birth_on_next_line(self):
        rects, stats = hits(
            "Состав многодетной семьи\n"
            "Место рождения\n"
            "дер. Верхнее Кукуево\n"
            "СНИЛС 123-456-789 00\n",
            aggressive_finance=True,
        )
        self.assertGreaterEqual(stats.get("birth", 0), 1, stats)
        self.assertTrue(rects)

    def test_next_label_stops_lookup(self):
        rects, stats = hits(
            "Место рождения\n"
            "Кем выдан\n"
            "ОВД Тверского района\n",
            aggressive_finance=True,
        )
        # «Кем выдан» — не значение места рождения.
        self.assertNotIn("birth", stats)

    def test_long_paragraph_is_not_treated_as_value(self):
        big = " ".join(["текст"] * 40)
        rects, stats = hits("Место рождения\n" + big + "\n", aggressive_finance=True)
        self.assertNotIn("birth", stats)


class HyphenatedNameTests(unittest.TestCase):
    """Составные имена и фамилии."""

    def test_double_first_name(self):
        line = lines_from_text("Также Мария-Луиза Казарян")[0]
        spans = [line.text[a:b] for a, b in find_names(line)]
        self.assertIn("Мария-Луиза Казарян", spans)

    def test_hyphenated_surname_untouched(self):
        line = lines_from_text("Сергеев-Кузнецов Дмитрий Олегович прибыл")[0]
        spans = [line.text[a:b] for a, b in find_names(line)]
        self.assertIn("Сергеев-Кузнецов Дмитрий Олегович", spans)

    def test_plant_is_not_a_name(self):
        line = lines_from_text("Иван-чай растёт у забора")[0]
        self.assertEqual(find_names(line), [])


class EmailAcrossLinesTests(unittest.TestCase):
    """E-mail, разорванный переносом строки."""

    def test_text_path(self):
        out, stats = anonymize_text(
            "e-mail: ivanov@\nexample.ru", "document", set(CATEGORIES)
        )
        self.assertNotIn("ivanov@", out)
        self.assertNotIn("example.ru", out)
        self.assertEqual(stats.get("contacts"), 1, stats)

    def test_org_kept_when_disabled(self):
        out, stats = anonymize_text("ООО «Ромашка»", "document", {"person"})
        self.assertIn("Ромашка", out)
        self.assertNotIn("org", stats)


if __name__ == "__main__":
    unittest.main()
