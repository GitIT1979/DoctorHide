# -*- coding: utf-8 -*-
"""Unit-тесты: finance (контекст предложения), find_names, anonymize_text."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from doctorhide.anonymize import anonymize_text
from doctorhide.categories import CATEGORIES
from doctorhide.finance import is_sensitive_number, mask_numbers
from doctorhide.names import find_names
from doctorhide.text_model import lines_from_text


class FinanceTokenTests(unittest.TestCase):
    def test_year_without_context_kept(self):
        self.assertFalse(is_sensitive_number("2024", "Решение от 2024 года по делу."))
        self.assertFalse(is_sensitive_number("1991", "В 1991 принят закон."))

    def test_year_birth_context_hidden(self):
        self.assertTrue(is_sensitive_number("1985", "Дата рождения 1985 год."))

    def test_article_page_kept(self):
        self.assertFalse(is_sensitive_number("123", "Согласно статье 123 ГК РФ."))
        self.assertFalse(is_sensitive_number("15", "Лист 15 тома 2."))

    def test_money_context(self):
        self.assertTrue(is_sensitive_number("1500", "Сумма 1500 руб."))
        self.assertTrue(is_sensitive_number("1 200 000", "Стоимость 1 200 000 рублей."))

    def test_mask_preserves_case_year(self):
        stats = {}
        out = mask_numbers("Решение от 2024 года. Сумма 1 200 000 руб.", stats)
        self.assertIn("2024", out)
        self.assertIn("[censored]", out)
        self.assertGreaterEqual(stats.get("finance", 0), 1)


class NameTests(unittest.TestCase):
    def test_full_fio(self):
        line = lines_from_text("Иванов Иван Иванович")[0]
        spans = find_names(line)
        self.assertTrue(spans)
        self.assertEqual(line.text[spans[0][0]:spans[0][1]], "Иванов Иван Иванович")

    def test_anonymize_person_and_inn(self):
        text = "Директор: Петров Пётр Петрович, ИНН 7707083893, год 2023"
        anon, stats = anonymize_text(text, "document", set(CATEGORIES))
        self.assertIn("[censored]", anon)
        self.assertNotIn("Петров", anon)
        self.assertNotIn("7707083893", anon)
        self.assertIn("2023", anon)  # год без birth/money-контекста
        self.assertIn("person", stats)
        self.assertIn("codes", stats)


class DocxKeepFormatTests(unittest.TestCase):
    def test_anonymize_docx_roundtrip(self):
        try:
            from docx import Document
        except ImportError:
            self.skipTest("python-docx not installed")
        from doctorhide.converters import anonymize_docx

        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "in.docx")
            dst = os.path.join(td, "out.docx")
            doc = Document()
            doc.add_paragraph("Исполнитель: Сидоров Сидор Сидорович")
            doc.add_paragraph("Год: 2024")
            doc.save(src)
            stats = anonymize_docx(src, dst, "document", set(CATEGORIES))
            out = Document(dst)
            joined = "\n".join(p.text for p in out.paragraphs)
            self.assertIn("[censored]", joined)
            self.assertNotIn("Сидоров", joined)
            self.assertIn("2024", joined)
            self.assertIn("person", stats)


class StatsConsistencyTests(unittest.TestCase):
    def test_inn_is_codes_not_only_identity(self):
        from doctorhide.detect import find_hits
        text = "ИНН 7707083893"
        anon, st = anonymize_text(text, "document", set(CATEGORIES))
        _, st2 = find_hits(lines_from_text(text), set(CATEGORIES), "document")
        self.assertIn("codes", st)
        self.assertIn("codes", st2)
        self.assertNotIn("7707083893", anon)

class TableAndImportIdTests(unittest.TestCase):
    def test_table_money_masks_all_numbers(self):
        from doctorhide.finance import mask_numbers
        table = (
            "| Позиция | Сумма | Валюта |\n"
            "| --- | --- | --- |\n"
            "| А | 150000 | руб |\n"
            "| Б | 2500 | |\n"
        )
        out = mask_numbers(table, {})
        self.assertNotIn("150000", out)
        self.assertNotIn("2500", out)

    def test_birth_abbrev(self):
        from doctorhide.anonymize import anonymize_text
        from doctorhide.categories import CATEGORIES
        anon, st = anonymize_text("д/р 15.03.1985", "document", set(CATEGORIES))
        self.assertNotIn("15.03.1985", anon)
        self.assertIn("birth", st)

    def test_iban(self):
        from doctorhide.anonymize import anonymize_text
        from doctorhide.categories import CATEGORIES
        anon, st = anonymize_text(
            "IBAN DE89 3704 0044 0532 0130 00", "document", set(CATEGORIES)
        )
        self.assertNotIn("3704", anon)
        self.assertIn("bank", st)

    def test_points_as_money(self):
        from doctorhide.finance import is_sensitive_number
        self.assertTrue(is_sensitive_number("1200", "Начислено 1200 баллов"))

class PdfTableLayoutTests(unittest.TestCase):
    def _line(self, cells, y=100.0, x0=50.0, gap=80.0):
        """cells: list of (text, width)."""
        from doctorhide.text_model import Line, Word, Rect

        words, starts, text = [], [], ""
        x = x0
        for tok, w in cells:
            if text:
                text += " "
            starts.append(len(text))
            text += tok
            words.append(Word(tok, Rect(x, y, x + w, y + 10)))
            x += gap
        return Line(text, words, starts)

    def test_detect_money_table_flags(self):
        from doctorhide.table_layout import detect_table_blocks, line_table_flags

        rows = [
            self._line([("Товар", 40), ("Сумма", 40), ("Валюта", 40)], y=50),
            self._line([("Услуга", 40), ("150000", 40), ("руб", 30)], y=65),
            self._line([("Доставка", 50), ("2500", 35), ("USD", 30)], y=80),
        ]
        blocks = detect_table_blocks(rows)
        self.assertTrue(blocks, "ожидался табличный блок")
        self.assertTrue(blocks[0].money)
        m, b, p = line_table_flags(rows[2], blocks)
        self.assertTrue(m)

    def test_find_hits_masks_all_table_numbers(self):
        from doctorhide.detect import find_hits
        from doctorhide.categories import CATEGORIES

        rows = [
            self._line([("Позиция", 40), ("Сумма", 40), ("Вал", 30)], y=50),
            self._line([("A", 20), ("150000", 50), ("руб", 30)], y=65),
            self._line([("B", 20), ("2500", 40), ("", 10)], y=80),
        ]
        hits, stats = find_hits(rows, set(CATEGORIES), aggressive_finance=True)
        fin_rects = [r for r, c in hits if c == "finance"]
        self.assertGreaterEqual(len(fin_rects), 2, stats)
        self.assertGreaterEqual(stats.get("finance", 0), 2)


    def test_y_form_label_above_value(self):
        """Подпись по y выше, значение ниже в той же колонке x."""
        from doctorhide.detect import find_hits
        from doctorhide.categories import CATEGORIES
        from doctorhide.table_layout import detect_table_blocks, line_table_flags

        label = self._line([("Сумма", 40)], y=50, x0=100)
        value = self._line([("150000", 50)], y=68, x0=100)
        blocks = detect_table_blocks([label, value])
        self.assertTrue(blocks)
        m, b, p = line_table_flags(value, blocks)
        self.assertTrue(m, "значение под «Сумма» должно наследовать money")
        hits, stats = find_hits([label, value], set(CATEGORIES), aggressive_finance=True)
        self.assertGreaterEqual(stats.get("finance", 0), 1, stats)

    def test_y_row_clustering_merges_split_lines(self):
        from doctorhide.table_layout import detect_table_blocks
        # две Line с почти одним y — одна полоса
        a = self._line([("Сумма", 40), ("руб", 25)], y=100, x0=50)
        b = self._line([("Итого", 40), ("5000", 35)], y=101.5, x0=50)
        c = self._line([("Аванс", 40), ("1000", 35)], y=118, x0=50)
        blocks = detect_table_blocks([a, b, c], y_tol=6.0)
        self.assertTrue(any(bl.money for bl in blocks))

class VerticalTextTests(unittest.TestCase):
    def test_is_vertical_rect(self):
        from doctorhide.vertical_text import is_vertical_rect
        from doctorhide.text_model import Rect
        self.assertTrue(is_vertical_rect(Rect(0, 0, 8, 40)))
        self.assertFalse(is_vertical_rect(Rect(0, 0, 40, 10)))

    def test_merge_vertical_letters(self):
        from doctorhide.vertical_text import merge_vertical_words
        from doctorhide.text_model import Rect, Word
        # буквы столбиком: И, Н, Н
        words = [
            Word("И", Rect(100, 10, 108, 22)),
            Word("Н", Rect(100, 24, 108, 36)),
            Word("Н", Rect(100, 38, 108, 50)),
        ]
        # узкие боксы, но ratio может быть ~1.5
        merged, rest = merge_vertical_words(words, min_ratio=1.2, min_chain=2)
        self.assertTrue(merged)
        text = merged[0].text.replace(" ", "")
        self.assertIn("И", text)
        self.assertIn("Н", text)
        self.assertEqual(len(text.replace(" ", "")), 3)

    def test_merge_vertical_digits_inn(self):
        from doctorhide.vertical_text import merge_vertical_words
        from doctorhide.text_model import Rect, Word
        digits = list("7707083893")
        words = []
        y = 10.0
        for d in digits:
            words.append(Word(d, Rect(50, y, 58, y + 12)))
            y += 14
        merged, _ = merge_vertical_words(words, min_ratio=1.2, min_chain=3)
        self.assertTrue(merged)
        joined = merged[0].text.replace(" ", "")
        self.assertEqual(joined, "7707083893")


class RegressionTests(unittest.TestCase):
    """Регрессии: падежи фамилий, ложный SWIFT, имена файлов."""

    def test_surname_dative_with_initials(self):
        for text in ("по иску Иванова И.И. к Петрову П.П.",
                     "с участием Петрова П.П.",
                     "в отношении Сидоровой А.В."):
            anon, stats = anonymize_text(text, "judicial", set(CATEGORIES))
            self.assertIn("person", stats, text)
            for surname in ("Иванова", "Петрову", "Петрова", "Сидоровой"):
                self.assertNotIn(surname + " ", anon, text)

    def test_common_words_not_names(self):
        for text in ("вину Петрова", "голову Ивана", "острове Русский", "долину реки"):
            anon, _ = anonymize_text(text, "document", set(CATEGORIES))
            self.assertEqual(anon, text)

    def test_swift_only_uppercase(self):
        for word in ("notified", "certificate", "application", "document", "protocol"):
            anon, _ = anonymize_text(word, "document", {"bank"})
            self.assertEqual(anon, word)
        for bic in ("SABRRUMM", "VTBRRUMMXXX", "DEUTDEFF"):
            anon, stats = anonymize_text("SWIFT " + bic, "document", {"bank"})
            self.assertNotIn(bic, anon)
            self.assertIn("bank", stats)

    def test_filename_keeps_latin_stem(self):
        from doctorhide.process import sanitize_filename

        for stem in ("reshenie", "vedomost", "dogovor", "akt"):
            self.assertEqual(sanitize_filename(stem, set(CATEGORIES), "document")[0], stem)
        self.assertEqual(
            sanitize_filename("Иванов Иван", set(CATEGORIES), "document")[0], "document"
        )


class DateNumberTests(unittest.TestCase):
    """Даты событий не должны рваться при маскировке чисел."""

    def test_date_after_money_stays(self):
        anon, _ = anonymize_text("Оплата 15 000 рублей до 31.12.2026",
                                 "document", set(CATEGORIES))
        self.assertIn("31.12.2026", anon)
        self.assertNotIn("15 000", anon)

    def test_date_without_money_stays(self):
        anon, _ = anonymize_text("Срок 01.07.25 по договору", "document", set(CATEGORIES))
        self.assertIn("01.07.25", anon)

    def test_decimal_money_still_masked(self):
        anon, _ = anonymize_text("Сумма 1234.56 руб.", "document", set(CATEGORIES))
        self.assertNotIn("1234.56", anon)


if __name__ == "__main__":
    unittest.main()
