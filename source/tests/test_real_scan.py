# -*- coding: utf-8 -*-
"""Интеграционный тест на реалистичном скане справки.

Отключён: сканы и OCR сняты с поддержки (см. README).
"""

from __future__ import annotations

import os
import sys
import unittest

if __name__ == "__main__":
    print("тест отключён: сканы и OCR сняты с поддержки")
    raise SystemExit(0)

raise unittest.SkipTest("сканы и OCR сняты с поддержки; см. README")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FIXTURE_PNG = os.path.join(ROOT, "tests", "fixtures", "scan_spravka.png")
FIXTURE_PDF = os.path.join(ROOT, "tests", "fixtures", "scan_spravka.pdf")


class RealScanOcrTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(FIXTURE_PNG):
            raise unittest.SkipTest(f"нет фикстуры {FIXTURE_PNG}")

    def test_ocr_text_contains_pii(self):
        from doctorhide.ocr import ocr_image, tesseract_available

        if not tesseract_available():
            self.skipTest("tesseract недоступен")
        text = ocr_image(FIXTURE_PNG, auto_rotate=True)
        self.assertIn("Петров", text)
        self.assertIn("7707083893", text)
        self.assertIn("Тверская", text)

    def test_ocr_words_have_cyrillic_and_boxes(self):
        from doctorhide.ocr import ocr_image_words, tesseract_available

        if not tesseract_available():
            self.skipTest("tesseract недоступен")
        words = ocr_image_words(FIXTURE_PNG, auto_rotate=True)
        texts = [t for t, *_ in words]
        self.assertTrue(any("Петров" in t for t in texts), texts[:20])
        self.assertTrue(any("7707083893" in t for t in texts))
        # bbox ненулевой
        for t, x0, y0, x1, y1 in words:
            if "7707083893" in t:
                self.assertGreater(x1, x0)
                self.assertGreater(y1, y0)

    def test_anonymize_scan_text(self):
        from doctorhide.anonymize import anonymize_text
        from doctorhide.categories import CATEGORIES
        from doctorhide.ocr import ocr_image, tesseract_available

        if not tesseract_available():
            self.skipTest("tesseract недоступен")
        text = ocr_image(FIXTURE_PNG)
        anon, stats = anonymize_text(text, "document", set(CATEGORIES))
        self.assertNotIn("Петров", anon)
        self.assertNotIn("7707083893", anon)
        self.assertIn("[censored]", anon)
        self.assertIn("person", stats)
        self.assertTrue(stats.get("codes") or stats.get("identity"))

    def test_rotated_scan_osd(self):
        from doctorhide.ocr import detect_orientation, ocr_image, tesseract_available

        if not tesseract_available():
            self.skipTest("tesseract недоступен")
        rot = os.path.join(ROOT, "tests", "fixtures", "scan_spravka_rot90.png")
        if not os.path.isfile(rot):
            self.skipTest("нет повёрнутой фикстуры")
        ang = detect_orientation(rot)
        self.assertIn(ang, (90, 270))
        text = ocr_image(rot, auto_rotate=True)
        self.assertTrue("Петров" in text or "7707083893" in text, text[:200])


class RealScanPdfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import pymupdf  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("pymupdf недоступен")
        if not os.path.isfile(FIXTURE_PDF):
            # собрать PDF из PNG при необходимости
            if not os.path.isfile(FIXTURE_PNG):
                raise unittest.SkipTest("нет фикстур")
            import pymupdf
            from PIL import Image

            im = Image.open(FIXTURE_PNG)
            w, h = im.size
            doc = pymupdf.open()
            page = doc.new_page(width=w * 72 / 200, height=h * 72 / 200)
            page.insert_image(page.rect, filename=FIXTURE_PNG)
            os.makedirs(os.path.dirname(FIXTURE_PDF), exist_ok=True)
            doc.save(FIXTURE_PDF)
            doc.close()

    def test_pdf_is_image_only(self):
        import pymupdf

        doc = pymupdf.open(FIXTURE_PDF)
        full = "".join(p.get_text() for p in doc).strip()
        doc.close()
        self.assertLess(len(full), 40)

    def test_redact_pdf_ocr_finds_pii_and_paints_bars(self):
        import tempfile
        import pymupdf
        from doctorhide.categories import CATEGORIES
        from doctorhide.pdf import redact_pdf_ocr

        with tempfile.TemporaryDirectory() as td:
            dst = os.path.join(td, "out.pdf")
            stats = redact_pdf_ocr(
                FIXTURE_PDF, dst, set(CATEGORIES), "document", dpi=150
            )
            self.assertTrue(os.path.isfile(dst))
            self.assertGreater(os.path.getsize(dst), 10_000)  # картинка сохранилась
            # ожидаем несколько категорий
            self.assertTrue(
                stats.get("person") or stats.get("codes") or stats.get("identity"),
                stats,
            )
            doc = pymupdf.open(dst)
            pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(1, 1), alpha=False)
            samples = pix.samples
            dark = sum(
                1
                for i in range(0, len(samples), 3)
                if samples[i] < 40 and samples[i + 1] < 40 and samples[i + 2] < 40
            )
            ratio = dark / (pix.width * pix.height)
            doc.close()
            self.assertGreater(ratio, 0.005, "ожидаются чёрные плашки на скан-странице")


if __name__ == "__main__":
    unittest.main()
