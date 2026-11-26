# -*- coding: utf-8 -*-
"""Регрессии P1: детекция в PDF и тексте (без OCR)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from doctorhide import CATEGORIES  # noqa: E402
from doctorhide.anonymize import anonymize_text  # noqa: E402
from doctorhide.detect import find_hits  # noqa: E402
from doctorhide.names import find_names  # noqa: E402
from doctorhide.pdf import redact_pdf  # noqa: E402
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


class LoneSurnameTests(unittest.TestCase):
    """Фамилия отдельным словом — значение поля в бланке."""

    def test_single_word_line(self):
        line = lines_from_text("Волкова")[0]
        self.assertEqual([line.text[a:b] for a, b in find_names(line)], ["Волкова"])

    def test_surname_in_sentence_untouched(self):
        line = lines_from_text("Иванова подпись стоит внизу")[0]
        self.assertEqual(find_names(line), [])

    def test_ordinary_word_untouched(self):
        line = lines_from_text("Приложение")[0]
        self.assertEqual(find_names(line), [])


class QuotesTests(unittest.TestCase):
    """Анонимизация организаций не должна портить типографику."""

    def test_quotes_preserved(self):
        out, stats = anonymize_text("ООО «Ромашка» и «Союз»", "document", set(CATEGORIES))
        self.assertIn("«", out)
        self.assertIn("»", out)
        self.assertNotIn("Ромашка", out)
        self.assertIn("«Союз»", out)


class AgeTests(unittest.TestCase):
    """«N лет» — возраст только при явном контексте."""

    def test_contract_term_not_age(self):
        anon, _ = anonymize_text("Срок договора 3 года с момента подписания",
                                 "document", set(CATEGORIES))
        self.assertIn("3 года", anon)

    def test_age_after_name_masked(self):
        anon, _ = anonymize_text("Петров Пётр, 45 лет, проживает", "document", set(CATEGORIES))
        self.assertNotIn("45 лет", anon)


class PhotoClosingTests(unittest.TestCase):
    """Портрет в бланке закрывается, широкая графика — нет."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _page_with(self, img_size, text):
        import pymupdf
        from PIL import Image

        photo = os.path.join(self.tmp, "img.png")
        Image.new("RGB", img_size, (198, 176, 150)).save(photo)
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        page.insert_font(fontname="F0", fontfile=r"C:\Windows\Fonts\arial.ttf")
        page.insert_text((60, 60), text, fontname="F0", fontsize=12)
        page.insert_image(pymupdf.Rect(60, 90, 200, 270), filename=photo)
        src = os.path.join(self.tmp, "src.pdf")
        doc.save(src)
        doc.close()
        return src

    def _crop_extrema(self, pdf, box):
        import pymupdf
        from PIL import Image

        doc = pymupdf.open(pdf)
        pix = doc[0].get_pixmap(dpi=72, colorspace=pymupdf.csGRAY, alpha=False)
        im = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        doc.close()
        return im.crop(box).getextrema()

    def test_portrait_closed_on_id_page(self):
        src = self._page_with((300, 400), "СВИДЕТЕЛЬСТВО О РОЖДЕНИИ")
        dst = os.path.join(self.tmp, "out.pdf")
        redact_pdf(src, dst, set(CATEGORIES), "document")
        self.assertLess(self._crop_extrema(dst, (80, 110, 180, 250))[1], 45)

    def test_plain_page_photo_kept(self):
        src = self._page_with((300, 400), "Отчёт о работе отдела")
        dst = os.path.join(self.tmp, "out2.pdf")
        redact_pdf(src, dst, set(CATEGORIES), "document")
        # без маркеров бланка портрет не трогаем
        self.assertGreater(self._crop_extrema(dst, (80, 110, 180, 250))[1], 150)


class CliExitCodeTests(unittest.TestCase):
    """Частичный успех не должен давать код 0."""

    def test_partial_batch_exits_nonzero(self):
        import shutil

        tmp = tempfile.mkdtemp()
        try:
            good = os.path.join(tmp, "good.md")
            bad = os.path.join(tmp, "bad.xyz")
            with open(good, "w", encoding="utf-8") as fh:
                fh.write("Иванов Иван Иванович\n")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("x")
            proc = subprocess.run(
                [sys.executable, os.path.join(ROOT, "doctorhide.py"), good, bad],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            self.assertNotEqual(proc.returncode, 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
