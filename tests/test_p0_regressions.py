# -*- coding: utf-8 -*-
"""Регрессии на исправленные P0-дефекты.

1. XLSX → Markdown: шапка и строки без цифр анонимизируются (раньше
   возвращались дословно).
2. XLSX --keep-format: числа (ИНН, счёт, телефон) проверяются правилами.
3. PDF: значения полей форм и вложения не переживают обезличивание.
4. GUI: статика не выходит за пределы gui/, чужой Host отклоняется.
5. CLI: -o при нескольких входах — ошибка, а не молчаливая перезапись.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from doctorhide import CATEGORIES  # noqa: E402
from doctorhide.process import process  # noqa: E402
from doctorhide.pdf import redact_pdf  # noqa: E402

BANNED = ("Иванов", "Петров", "Васильев", "7707083893", "40817810099910004312",
          "+7 999", "999 111")


def _args(out: str, **kw) -> argparse.Namespace:
    base = dict(mode="document", out=out, keep=[], only=[], ask=False,
                keep_format=False, keep_name=True)
    base.update(kw)
    return argparse.Namespace(**base)


class XlsxToMarkdownTests(unittest.TestCase):
    """P0-1: строки таблицы без цифр больше не обходят маскировку."""

    def setUp(self):
        import openpyxl

        self.tmp = tempfile.mkdtemp()
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Иванов Иван Иванович", "Петров Пётр Петрович"])
        ws.append(["Васильев Василий Васильевич", "г. Москва, ул. Тверская"])
        ws.append([1000, 2000])
        self.xlsx = os.path.join(self.tmp, "tab.xlsx")
        wb.save(self.xlsx)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_header_and_text_rows_are_masked(self):
        out = os.path.join(self.tmp, "out.md")
        rep = process(self.xlsx, _args(out), None)
        self.assertEqual(rep["status"], "ok")
        with open(out, encoding="utf-8") as fh:
            text = fh.read()
        left = [b for b in BANNED if b in text]
        self.assertEqual(left, [], f"в результате остались ПДн: {left}")
        # структура таблицы не разрушена
        self.assertIn("|", text)
        self.assertIn("---", text)
        self.assertIn("[censored]", text)


class XlsxKeepFormatTests(unittest.TestCase):
    """P0-2: числа в ячейках проверяются теми же правилами."""

    def setUp(self):
        import openpyxl

        self.tmp = tempfile.mkdtemp()
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["ФИО", "ИНН", "Счёт", "Телефон"])
        ws.append(["Иванов Иван Иванович", 7707083893,
                   40817810099910004312, "+7 999 111-22-33"])
        self.xlsx = os.path.join(self.tmp, "num.xlsx")
        wb.save(self.xlsx)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_numeric_identifiers_are_masked(self):
        import openpyxl

        out = os.path.join(self.tmp, "num_hidden.xlsx")
        rep = process(self.xlsx, _args(out, keep_format=True), None)
        self.assertEqual(rep["status"], "ok")
        wb = openpyxl.load_workbook(out)
        row = [c for c in next(wb.active.iter_rows(min_row=2, max_row=2, values_only=True))]
        joined = " ".join(str(v) for v in row)
        for needle in ("7707083893", "40817810099910004312", "999 111"):
            self.assertNotIn(needle, joined)
        self.assertIn("[censored]", joined)


class PdfFormTests(unittest.TestCase):
    """P0-3: поля форм, аннотации и вложения не переживают редакцию."""

    def setUp(self):
        import pymupdf

        self.pymupdf = pymupdf
        self.tmp = tempfile.mkdtemp()
        self.src = os.path.join(self.tmp, "form.pdf")
        self.dst = os.path.join(self.tmp, "form_hidden.pdf")

        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 100), "Анкета: Иванов Иван Иванович")
        widget = pymupdf.Widget()
        widget.field_name = "familiya"
        widget.field_value = "Петров Пётр Петрович"
        widget.rect = pymupdf.Rect(72, 120, 320, 140)
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        page.add_widget(widget)
        annot = page.add_text_annot((72, 200), "звонить Иванову И.И.")
        annot.set_info(title="Иванов И.И.")
        annot.update()
        doc.embfile_add("passport.txt", "Иванов Иван Иванович 4509 123456".encode())
        doc.save(self.src)
        doc.close()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fields_annotations_attachments(self):
        redact_pdf(self.src, self.dst, set(CATEGORIES), "document")
        doc = self.pymupdf.open(self.dst)
        try:
            values = [w.field_value for w in doc[0].widgets()]
            for value in values:
                self.assertNotIn("Петров", str(value), f"поле формы не закрыто: {value}")

            self.assertEqual(doc.embfile_names(), [], "вложение осталось в PDF")

            annot_text = " ".join(
                str(a.info.get("content", "")) + " " + str(a.info.get("title", ""))
                for a in doc[0].annots()
            )
            self.assertNotIn("Иванов", annot_text, f"аннотация не закрыта: {annot_text}")

            page_text = "".join(p.get_text() for p in doc)
            self.assertNotIn("Иванов", page_text, "текст на странице не закрыт")
        finally:
            doc.close()


class GuiStaticAndHostTests(unittest.TestCase):
    """P0-4: обход каталога и проверка Host."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(ROOT, "gui", "server.py")
        spec = importlib.util.spec_from_file_location("doctorhide_gui_server", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.mod = module
        cls.gui_dir = os.path.dirname(os.path.abspath(path))

    def test_traversal_is_blocked(self):
        self.assertIsNone(self.mod._static_file(self.gui_dir, "/../_gui_work/in/x.png"))
        self.assertIsNone(self.mod._static_file(self.gui_dir, "/..%2f_gui_work/in/x.png"))
        self.assertIsNone(self.mod._static_file(self.gui_dir, "/../README.md"))

    def test_own_file_is_served(self):
        got = self.mod._static_file(self.gui_dir, "/spy_pipboy_200x200.gif")
        self.assertIsNotNone(got)
        self.assertTrue(got.endswith("spy_pipboy_200x200.gif"))

    def _handler(self, host: str):
        fake = type("Fake", (), {"headers": {"Host": host}})
        fake._host_ok = self.mod.Handler._host_ok
        return fake()

    def test_host_check(self):
        self.assertTrue(self._handler("127.0.0.1:8765")._host_ok())
        self.assertTrue(self._handler("localhost:8765")._host_ok())
        self.assertFalse(self._handler("evil.example:8765")._host_ok())
        self.assertFalse(self._handler("")._host_ok())


class CliOutTests(unittest.TestCase):
    """P0-5: -o при нескольких входах — явная ошибка."""

    def test_multiple_inputs_with_out_fail(self):
        tmp = tempfile.mkdtemp()
        try:
            a = os.path.join(tmp, "a.md")
            b = os.path.join(tmp, "b.md")
            out = os.path.join(tmp, "same.md")
            with open(a, "w", encoding="utf-8") as fh:
                fh.write("Иванов Иван Иванович\n")
            with open(b, "w", encoding="utf-8") as fh:
                fh.write("Петров Пётр Петрович\n")
            proc = subprocess.run(
                [sys.executable, os.path.join(ROOT, "doctorhide.py"), a, b, "-o", out],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse(os.path.exists(out), "файл не должен создаваться")
            self.assertIn("--out", proc.stderr)
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
