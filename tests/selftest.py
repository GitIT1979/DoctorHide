#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Самопроверка DoctorHide.

Собирает фикстуры (md, docx, pdf с текстом, скан без текста, png) и прогоняет их
через настоящий CLI, проверяя результат: что скрыто, что сохранено, читается ли файл
и не осталось ли данных под плашками.

Запуск:
    python tests/selftest.py
    python tests/selftest.py -v      # подробно
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pymupdf
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "doctorhide.py")
WORK = os.path.join(ROOT, "_private", "_selftest")

VERBOSE = "-v" in sys.argv

DOC_MD = """Договор оказания услуг № 12/2024

Исполнитель: ООО «Пример», ИНН 7707083893, КПП 770701001, ОГРН 1027700132195
Адрес: 125009, г. Москва, ул. Тверская, д. 15, кв. 42
Директор: Иванов Иван Иванович, тел. +7 (495) 123-45-67, e-mail: ivanov@example.ru
Сотрудник Петров Пётр Петрович, 35 лет, паспорт 4509 123456, СНИЛС 112-233-445 95
Банк: ПАО «Сбербанк», р/с 40702810123456789012, БИК 044525225
Выручка за квартал: 1 200 000 руб.
"""

COURT_MD = """РЕШЕНИЕ
Именем Российской Федерации

Кировский районный суд г. Москвы в составе председательствующего судьи
Сидорова А.В., при секретаре Кузнецовой М.И., рассмотрев дело № 2-123/2023
по иску Иванова И.И. к Петрову П.П., 45 лет, проживающего по адресу:
г. Москва, ул. Ленина, д. 5, кв. 10, с участием прокурора Смирнова А.А.
установил:
"""

SCHOOL_MD = """Директору ГБОУ СОШ № 42 Центрального района г. Твери Петровой А.И.
от Сергеева-Кузнецова Дмитрия Олеговича, паспорт 2812 445566

Заявление

Прошу разрешить Сергееву-Кузнецову Артёму Дмитриевичу, ученику 7 Г класса,
в связи с его участием в мероприятиях в Театре юношеского творчества Тверского
городского Дворца творчества юных ранний уход с занятий 15 апреля 2026 года.
"""

BANNED_DOC = ["Иванов", "Петров", "7707083893", "770701001", "1027700132195",
              "125009", "Москва", "Тверская", "123-45-67", "ivanov@", "4509",
              "123456", "112-233-445", "1 200 000", "40702810123456789012"]

results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    results.append((bool(ok), name, detail))
    mark = "OK  " if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail and (VERBOSE or not ok) else ""))


def run(args, stdin_text=None, keep_name=True):
    tail = ["--keep-name"] if keep_name and "-o" not in args else []
    return subprocess.run([sys.executable, APP] + args + tail, cwd=WORK, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", input=stdin_text)


def pdf_lines(path, lines, fontsize=11):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    font = r"C:\Windows\Fonts\arial.ttf"
    page.insert_font(fontname="F0", fontfile=font)
    y = 60
    for line in lines:
        if line:
            page.insert_text((60, y), line, fontname="F0", fontsize=fontsize)
        y += 18
    doc.save(path)
    return path


def pdf_text(path):
    doc = pymupdf.open(path)
    text = "".join(p.get_text() for p in doc)
    pages = doc.page_count
    meta = {k: v for k, v in doc.metadata.items() if v}
    doc.close()
    return text, pages, meta


def norm_text(text):
    """Мягкие переносы, тире и неразрывные пробелы -> обычные символы.

    В PDF дефис часто восстанавливается как U+00AD, и проверка «123-45-67» без
    нормализации проходит вхолостую — данные при этом остаются в файле.
    """
    return (text.replace("\xad", "-").replace("\u2010", "-").replace("\u2011", "-")
            .replace("\u2012", "-").replace("\u2013", "-").replace("\u2014", "-")
            .replace("\u00a0", " ").replace("\u2009", " ").replace("\u202f", " "))


def pdf_streams(path):
    """Видимый текст: сырые байты файла и потоки содержимого страниц.

    Шрифтовые программы и растры не проверяем — там встречаются те же цифры
    в служебных таблицах глифов, это не утечка.
    """
    doc = pymupdf.open(path)
    blobs = [open(path, "rb").read()]
    for page in doc:
        try:
            blobs.append(page.read_contents() or b"")
        except Exception:
            pass
    doc.close()
    return b"\n".join(blobs).decode("utf-8", "replace")


def whited_pixels(src, dst):
    """Сколько пикселей стало чёрным (плашка теперь чёрная, как в кино)."""
    a = Image.open(src).convert("L")
    b = Image.open(dst).convert("L")
    if a.size != b.size:
        return -1
    pa, pb = a.load(), b.load()
    w, h = a.size
    count = 0
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            if pa[x, y] > 190 and pb[x, y] < 80:
                count += 1
    return count


def main() -> int:
    os.makedirs(WORK, exist_ok=True)
    for name in os.listdir(WORK):
        try:
            os.remove(os.path.join(WORK, name))
        except OSError:
            pass

    print("== окружение ==")
    check(os.path.exists(APP), "программа на месте", APP)
    tess = os.path.join(ROOT, "_tools", "tessdata", "rus.traineddata")
    check(os.path.exists(tess), "русская модель Tesseract найдена", tess)

    with open(os.path.join(WORK, "doc.md"), "w", encoding="utf-8") as f:
        f.write(DOC_MD)
    with open(os.path.join(WORK, "court.md"), "w", encoding="utf-8") as f:
        f.write(COURT_MD)

    with open(os.path.join(WORK, "school.md"), "w", encoding="utf-8") as f:
        f.write(SCHOOL_MD)

    from docx import Document

    d = Document()
    d.add_heading("Акт проверки", level=1)
    d.add_paragraph("Организация: АО «Вектор», ИНН 7701234567")
    d.add_paragraph("Директор: Смирнов Сергей Сергеевич, 52 года.")
    d.save(os.path.join(WORK, "doc.docx"))

    lines = DOC_MD.splitlines()
    pdf_lines(os.path.join(WORK, "doc.pdf"), lines)

    src = pymupdf.open(os.path.join(WORK, "doc.pdf"))
    pix = src[0].get_pixmap(dpi=200)
    png = os.path.join(WORK, "doc.png")
    pix.save(png)
    scan = pymupdf.open()
    sp = scan.new_page(width=595, height=842)
    sp.insert_image(sp.rect, filename=png)
    scan.save(os.path.join(WORK, "scan.pdf"))
    scan.close()
    src.close()

    print("\n== md ==")
    r = run(["doc.md"])
    out_md = os.path.join(WORK, "doc_hidden.md")
    check(r.returncode == 0, "код возврата 0", r.stderr.strip()[:120])
    check(os.path.exists(out_md), "создан doc_hidden.md")
    if os.path.exists(out_md):
        text = open(out_md, encoding="utf-8").read()
        left = [b for b in BANNED_DOC if b in text]
        check(not left, "персональные данные скрыты", f"осталось: {left}")
        check("[censored]" in text, "маркер [censored] на месте")
        check("Договор оказания услуг" in text, "неизменный текст сохранён")

    print("\n== pdf с текстовым слоем ==")
    r = run(["doc.pdf"])
    out_pdf = os.path.join(WORK, "doc_hidden.pdf")
    check(r.returncode == 0, "код возврата 0", r.stderr.strip()[:120])
    check(os.path.exists(out_pdf), "создан doc_hidden.pdf")
    if os.path.exists(out_pdf):
        text, pages, meta = pdf_text(out_pdf)
        flat = norm_text(text)
        left = [b for b in BANNED_DOC if b in flat]
        check(not left, "в тексте нет скрытых данных", f"осталось: {left}")
        check(pages == 1, "число страниц сохранено", str(pages))
        check(len(text) > 80, "документ остаётся читаемым", f"{len(text)} символов")
        meta_core = {k: v for k, v in meta.items() if k != "format"}
        check(not meta_core, "метаданные очищены", str(meta_core))
        streams = norm_text(pdf_streams(out_pdf))
        left_s = [b for b in BANNED_DOC if b in streams]
        check(not left_s, "в потоках PDF нет скрытых данных", f"осталось: {left_s}")

    print("\n== PDF всегда остаётся PDF ==")
    r = run(["doc.pdf", "-o", "wrong.md"])
    fixed = os.path.join(WORK, "wrong.pdf")
    check(os.path.exists(fixed), "результат сохранён как .pdf", f"код {r.returncode}")
    if os.path.exists(fixed):
        head = open(fixed, "rb").read(5)
        check(head == b"%PDF-", "внутри действительно PDF", repr(head))
        check(not os.path.exists(os.path.join(WORK, "wrong.md")), "файл с чужим расширением не создан")

    print("\n== судебный акт ==")
    r = run(["court.md"])
    out_court = os.path.join(WORK, "court_hidden.md")
    check(r.returncode == 0, "код возврата 0", r.stderr.strip()[:120])
    if os.path.exists(out_court):
        text = open(out_court, encoding="utf-8").read()
        kept = ["Сидорова А.В.", "Кузнецовой М.И.", "Смирнова А.А.", "Иванова И.И.", "Кировский районный суд", "2-123/2023"]
        missed = [k for k in kept if k not in text]
        check(not missed, "суд и участники сохранены", f"пропало: {missed}")
        hidden = [x for x in ["Москва", "45 лет"] if x in text]
        check(not hidden, "адрес/город/возраст скрыты", f"осталось: {hidden}")

    print("\n== школьное заявление ==")
    r = run(["school.md"])
    out_school = os.path.join(WORK, "school_hidden.md")
    check(r.returncode == 0, "код возврата 0", r.stderr.strip()[:120])
    if os.path.exists(out_school):
        text = open(out_school, encoding="utf-8").read()
        banned = ["Петровой", "Сергеев", "Кузнецов", "2812", "445566", "ГБОУ", "СОШ",
                  "Театр", "Твери"]
        left = [b for b in banned if b in text]
        check(not left, "школа, ФИО, паспорт и театр скрыты", f"осталось: {left}")
        check("Заявление" in text and "7 Г класса" in text, "текст заявления сохранён")

    print("\n== docx ==")
    r = run(["doc.docx"])
    out_docx = os.path.join(WORK, "doc_hidden.md")
    check(r.returncode == 0, "код возврата 0", r.stderr.strip()[:120])
    if os.path.exists(out_docx):
        text = open(out_docx, encoding="utf-8").read()
        check("# Акт проверки" in text, "заголовок перенесён в Markdown")
        check("7701234567" not in text, "ИНН скрыт")
        check("Смирнов" not in text, "ФИО скрыто")

    print("\n== скан без текстового слоя ==")
    r = run(["scan.pdf"])
    out_scan = os.path.join(WORK, "scan_hidden.pdf")
    check(r.returncode == 0, "код возврата 0", r.stderr.strip()[:120])
    if os.path.exists(out_scan):
        text, pages, _ = pdf_text(out_scan)
        check(pages == 1, "страница сохранена", str(pages))
        check(len(text.strip()) > 50, "текстовый слой добавлен", f"{len(text)} символов")
        flat = norm_text(text)
        left = [b for b in BANNED_DOC if b in flat]
        check(not left, "в текстовом слое нет скрытых данных", f"осталось: {left}")
        shot = os.path.join(WORK, "scan_hidden.png")
        pymupdf.open(out_scan)[0].get_pixmap(dpi=200).save(shot)
        whited = whited_pixels(os.path.join(WORK, "doc.png"), shot)
        check(whited > 300, "пиксели данных выжжены", f"закрыто точек: {whited}")

    print("\n== изображение ==")
    r = run(["doc.png"])
    out_img = os.path.join(WORK, "doc_hidden.png")
    check(r.returncode == 0, "код возврата 0", r.stderr.strip()[:120])
    check(os.path.exists(out_img), "создан doc_hidden.png")
    if os.path.exists(out_img):
        check(Image.open(out_img).size == Image.open(png).size, "размер сохранён")
        whited = whited_pixels(png, out_img)
        check(whited > 300, "пиксели данных выжжены", f"закрыто точек: {whited}")

    print("\n== фамилия, разорванная OCR ==")
    # сканер читает «ЯКОВЛЕВА-ЗАВАЛЬЕВСКАЯ» двумя кусками: раньше закрывался только хвост
    pdf_lines(os.path.join(WORK, "torn.pdf"),
              ["Выписка из распоряжения",
               "признан(а)  ЯКОВЛЕВА-ЗАВАЛЬ ЕВСКАЯ С.А.",
               "Начальник отдела  Е.А.Решетняк"])
    r = run(["torn.pdf"])
    out_torn = os.path.join(WORK, "torn_hidden.pdf")
    check(r.returncode == 0 and os.path.exists(out_torn), "код возврата 0", r.stderr.strip()[:120])
    if os.path.exists(out_torn):
        text, _, _ = pdf_text(out_torn)
        flat = norm_text(text)
        left = [x for x in ("ЯКОВЛЕВА", "ЗАВАЛЬ", "ЕВСКАЯ", "Решетняк") if x in flat]
        check(not left, "куски фамилии закрыты целиком", f"осталось: {left}")

    print("\n== таблица: подпись сверху, значение снизу ==")
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_font(fontname="F0", fontfile=r"C:\Windows\Fonts\arial.ttf")
    page.insert_text((60, 80), "Состав многодетной семьи", fontname="F0", fontsize=13)
    page.insert_text((60, 120), "Дата рождения", fontname="F0", fontsize=10)
    page.insert_text((60, 136), "20.09.1977", fontname="F0", fontsize=11)
    page.insert_text((330, 120), "СНИЛС", fontname="F0", fontsize=10)
    page.insert_text((330, 136), "123-456-789 00", fontname="F0", fontsize=11)
    page.insert_text((60, 200), "Место рождения", fontname="F0", fontsize=10)
    page.insert_text((60, 216), "дер. Верхнее Кукуево", fontname="F0", fontsize=11)
    doc.save(os.path.join(WORK, "table.pdf"))
    doc.close()
    r = run(["table.pdf"])
    out_tab = os.path.join(WORK, "table_hidden.pdf")
    check(r.returncode == 0 and os.path.exists(out_tab), "код возврата 0", r.stderr.strip()[:120])
    if os.path.exists(out_tab):
        text, _, _ = pdf_text(out_tab)
        flat = norm_text(text)
        left = [x for x in ("20.09.1977", "123-456-789", "Кукуево") if x in flat]
        check(not left, "значения под подписями закрыты", f"осталось: {left}")
        check("Дата рождения" in flat and "Состав многодетной семьи" in flat,
              "подписи и заголовок остались")

    print("\n== свидетельство: значения закрыты, бланк читается, фото тоже ==")
    photo = os.path.join(WORK, "photo.png")
    Image.new("RGB", (300, 400), (198, 176, 150)).save(photo)
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_font(fontname="F0", fontfile=r"C:\Windows\Fonts\arial.ttf")
    page.insert_text((150, 60), "СВИДЕТЕЛЬСТВО О РОЖДЕНИИ", fontname="F0", fontsize=14)
    page.insert_image(pymupdf.Rect(60, 90, 200, 270), filename=photo)
    page.insert_text((250, 110), "Кузнецова", fontname="F0", fontsize=11)
    page.insert_text((60, 320), "Дата рождения  03.04.1991", fontname="F0", fontsize=11)
    page.insert_text((60, 340), "Кем выдан  ОВД Тверского района", fontname="F0", fontsize=11)
    doc.save(os.path.join(WORK, "svidetelstvo.pdf"))
    doc.close()
    r = run(["svidetelstvo.pdf"])
    out_sv = os.path.join(WORK, "svidetelstvo_hidden.pdf")
    check(r.returncode == 0 and os.path.exists(out_sv), "код возврата 0", r.stderr.strip()[:120])
    if os.path.exists(out_sv):
        text, pages, _ = pdf_text(out_sv)
        flat = norm_text(text)
        left = [x for x in ("Кузнецова", "03.04.1991", "Тверского", "ОВД") if x in flat]
        check(not left, "поля бланка закрыты", f"осталось: {left}")
        check("СВИДЕТЕЛЬСТВО О РОЖДЕНИИ" in flat, "заголовок бланка на месте")
        check(pages == 1, "страница сохранена", str(pages))
        doc2 = pymupdf.open(out_sv)
        pix = doc2[0].get_pixmap(dpi=72, colorspace=pymupdf.csGRAY, alpha=False)
        im = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        doc2.close()
        low = im.crop((80, 110, 180, 250)).getextrema()
        check(low[1] < 45, "фотография закрыта", f"самый светлый пиксель {low[1]}")
        readable = [x for x in ("СВИДЕТЕЛЬСТВО О РОЖДЕНИИ", "Дата рождения", "Кем выдан")
                    if x in flat]
        check(len(readable) >= 2, "бланк остаётся узнаваемым", f"видно: {readable}")

    print("\n== скан паспорта: фотография и поля ==")
    scan_src = os.path.join(WORK, "svidetelstvo.pdf")
    png = os.path.join(WORK, "passport_page.png")
    pymupdf.open(scan_src)[0].get_pixmap(dpi=200).save(png)
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, filename=png)
    doc.save(os.path.join(WORK, "passport_scan.pdf"))
    doc.close()
    r = run(["passport_scan.pdf"])
    out_scan2 = os.path.join(WORK, "passport_scan_hidden.pdf")
    check(r.returncode == 0 and os.path.exists(out_scan2), "код возврата 0", r.stderr.strip()[:120])
    if os.path.exists(out_scan2):
        text, pages, _ = pdf_text(out_scan2)
        flat = norm_text(text)
        left = [x for x in ("Кузнецова", "03.04.1991", "Тверского") if x in flat]
        check(not left, "поля на скане закрыты", f"осталось: {left}")
        doc2 = pymupdf.open(out_scan2)
        pix = doc2[0].get_pixmap(dpi=72, colorspace=pymupdf.csGRAY, alpha=False)
        im = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        doc2.close()
        low = im.crop((85, 115, 175, 245)).getextrema()
        check(low[1] < 60, "фотография на скане закрыта", f"самый светлый пиксель {low[1]}")

    print("\n== редкое имя без суффикса и без инициалов ==")
    pdf_lines(os.path.join(WORK, "rare.pdf"),
              ["Заявление",
               "Прошу зачислить Сафронов Айгуль на обучение.",
               "Директор школы  Ярцев К.К."])
    r = run(["rare.pdf"])
    out_rare = os.path.join(WORK, "rare_hidden.pdf")
    check(r.returncode == 0 and os.path.exists(out_rare), "код возврата 0", r.stderr.strip()[:120])
    if os.path.exists(out_rare):
        text, _, _ = pdf_text(out_rare)
        flat = norm_text(text)
        left = [x for x in ("Сафронов", "Айгуль", "Ярцев") if x in flat]
        check(not left, "имя рядом с фамилией закрыто", f"осталось: {left}")

    print("\n== выбор категорий ==")
    r = run(["doc.md", "--keep", "person", "-o", "keep.md"])
    if os.path.exists(os.path.join(WORK, "keep.md")):
        text = open(os.path.join(WORK, "keep.md"), encoding="utf-8").read()
        check("Иванов Иван Иванович" in text, "--keep person оставляет ФИО")
        check("7707083893" not in text, "остальные категории скрыты")

    r = run(["doc.md", "--ask", "-o", "ask.md"], stdin_text="person\n")
    if os.path.exists(os.path.join(WORK, "ask.md")):
        text = open(os.path.join(WORK, "ask.md"), encoding="utf-8").read()
        check("Иванов Иван Иванович" in text, "--ask учитывает ответ")

    print("\n== имена файлов ==")
    with open(os.path.join(WORK, "Заявление Иванова 01.02.2023.md"), "w", encoding="utf-8") as f:
        f.write("Прошу разрешить отсутствие 1 февраля.\n")
    r = run(["Заявление Иванова 01.02.2023.md"], keep_name=False)
    hidden = [f for f in os.listdir(WORK) if f.endswith("_hidden.md")]
    check(r.returncode == 0 and bool(hidden), "имя с ПДн -> _hidden", str(hidden[:2]))

    r = run(["doc.md"], keep_name=False)
    plain = [f for f in os.listdir(WORK) if f.endswith("_hidden.md")]
    check(r.returncode == 0 and bool(plain), "чистое имя -> _hidden", str(plain[:2]))

    print("\n== имена результатов ==")
    produced = [f for f in os.listdir(WORK) if os.path.isfile(os.path.join(WORK, f)) and "_hidden" in f]
    anon_named = [f for f in os.listdir(WORK) if "_anon" in f]
    check(not anon_named, "ни одного файла с _anon", str(anon_named[:5]))
    check(len(produced) >= 6 and all("_hidden" in f for f in produced),
          "у всех результатов суффикс _hidden", f"{len(produced)} файлов")

    print("\n== отчёт и ошибки ==")
    r = run(["doc.md", "-o", "rep.md", "--report", "report.json"])
    rep_path = os.path.join(WORK, "report.json")
    ok = os.path.exists(rep_path)
    check(ok, "JSON-отчёт создан")
    if ok:
        data = json.load(open(rep_path, encoding="utf-8"))
        cats = data[0].get("categories", {})
        check("person" in cats and "codes" in cats, "в отчёте есть категории", str(list(cats)))

    open(os.path.join(WORK, "bad.xyz"), "w").write("test")
    r = run(["bad.xyz"])
    check(r.returncode != 0, "неподдерживаемый формат отклонён", f"код {r.returncode}")

    r = run(["нет-такого-файла.pdf"])
    check(r.returncode != 0, "отсутствующий файл обработан как ошибка", f"код {r.returncode}")

    print("\n== ФИО на разных строках, с пустой строкой между частями ==")
    pdf_lines(os.path.join(WORK, "splitname.pdf"),
              ["Заявление",
               "Исполнитель",
               "Кузнецов",
               "",
               "Алексей Дмитриевич"])
    r = run(["splitname.pdf"])
    out_sp = os.path.join(WORK, "splitname_hidden.pdf")
    check(r.returncode == 0 and os.path.exists(out_sp), "код возврата 0", r.stderr.strip()[:120])
    if os.path.exists(out_sp):
        text, _, _ = pdf_text(out_sp)
        flat = norm_text(text)
        left = [x for x in ("Кузнецов", "Алексей", "Дмитриевич") if x in flat]
        check(not left, "ФИО через разрыв строк закрыто целиком", f"осталось: {left}")

    print("\n== ФИО на разных строках в md ==")
    with open(os.path.join(WORK, "splitname.md"), "w", encoding="utf-8") as f:
        f.write("Заявление\n\nПодпись\n\nВолкова\n\nОльга Сергеевна\n\nДата\n")
    r = run(["splitname.md"])
    out_spm = os.path.join(WORK, "splitname_hidden.md")
    check(os.path.exists(out_spm), "создан splitname_hidden.md")
    if os.path.exists(out_spm):
        text = open(out_spm, encoding="utf-8").read()
        left = [x for x in ("Волкова", "Ольга", "Сергеевна") if x in text]
        check(not left, "ФИО через разрыв строк в md закрыто", f"осталось: {left}")
        check("Заявление" in text and "Дата" in text, "текст документа сохранён")

    print("\n== двойные имена ==")
    pdf_lines(os.path.join(WORK, "double.pdf"),
              ["Студентка Ветрова Софья-Анна учится",
               "Также Мария-Луиза Казарян"])
    r = run(["double.pdf"])
    out_db = os.path.join(WORK, "double_hidden.pdf")
    check(r.returncode == 0 and os.path.exists(out_db), "код возврата 0", r.stderr.strip()[:120])
    if os.path.exists(out_db):
        text, _, _ = pdf_text(out_db)
        flat = norm_text(text)
        left = [x for x in ("Ветрова", "Софья-Анна", "Мария-Луиза", "Казарян") if x in flat]
        check(not left, "двойные имена закрыты целиком", f"осталось: {left}")

    print("\n== все даты скрываются ==")
    with open(os.path.join(WORK, "dates.md"), "w", encoding="utf-8") as f:
        f.write("Собрание 15.03.2024. Дедлайн 01.07.25. Отчёт за 2024 год.\n")
    r = run(["dates.md"])
    out_dt = os.path.join(WORK, "dates_hidden.md")
    check(os.path.exists(out_dt), "создан dates_hidden.md")
    if os.path.exists(out_dt):
        text = open(out_dt, encoding="utf-8").read()
        left = [x for x in ("15.03.2024", "01.07.25", "2024 год") if x in text]
        check(not left, "числовые даты и год скрыты", f"осталось: {left}")

    print("\n== e-mail через разрыв строки ==")
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_font(fontname="F0", fontfile=r"C:\Windows\Fonts\arial.ttf")
    page.insert_text((60, 100), "e-mail: ivanov@", fontname="F0", fontsize=11)
    page.insert_text((60, 118), "example.ru", fontname="F0", fontsize=11)
    doc.save(os.path.join(WORK, "mail.pdf"))
    doc.close()
    r = run(["mail.pdf"])
    out_ml = os.path.join(WORK, "mail_hidden.pdf")
    check(r.returncode == 0 and os.path.exists(out_ml), "код возврата 0", r.stderr.strip()[:120])
    if os.path.exists(out_ml):
        text, _, _ = pdf_text(out_ml)
        flat = norm_text(text)
        left = [x for x in ("ivanov@", "example.ru") if x in flat]
        check(not left, "e-mail через разрыв строки закрыт", f"осталось: {left}")

    print("\n== телефон без кода страны и счёт с пробелами ==")
    with open(os.path.join(WORK, "contacts.md"), "w", encoding="utf-8") as f:
        f.write("тел. 495 123-45-67, р/с 4070 2810 1234 5678 9012 в банке\n")
    r = run(["contacts.md"])
    out_ct = os.path.join(WORK, "contacts_hidden.md")
    check(os.path.exists(out_ct), "создан contacts_hidden.md")
    if os.path.exists(out_ct):
        text = open(out_ct, encoding="utf-8").read()
        left = [x for x in ("495 123-45-67", "4070 2810 1234 5678 9012") if x in text]
        check(not left, "телефон без +7 и счёт с пробелами скрыты", f"осталось: {left}")

    print("\n== штрихкод в картинке ==")
    code = Image.new("RGB", (900, 1200), (255, 255, 255))
    dd = ImageDraw.Draw(code)
    x = 120
    while x < 700:
        dd.rectangle([x, 480, x + 3, 600], fill=(0, 0, 0))
        x += 6
    src_code = os.path.join(WORK, "barcode.png")
    code.save(src_code)
    r = run(["barcode.png"])
    out_bc = os.path.join(WORK, "barcode_hidden.png")
    check(r.returncode == 0 and os.path.exists(out_bc), "код возврата 0", r.stderr.strip()[:120])
    if os.path.exists(out_bc):
        im = Image.open(out_bc).convert("L")
        dark = im.crop((100, 520, 720, 560)).getextrema()
        check(dark[1] < 80, "штрихкод в картинке закрыт плашкой", f"самый светлый пиксель {dark[1]}")

    print("\n== устаревший бинарный .doc отклонён ==")
    with open(os.path.join(WORK, "old.doc"), "wb") as f:
        f.write(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64)
    r = run(["old.doc"])
    check(r.returncode != 0 and not os.path.exists(os.path.join(WORK, "old_hidden.doc")),
          "бинарный .doc отклонён с понятной ошибкой", r.stderr.strip()[:160])

    print("\n== белые плашки по флагу --white-bars ==")
    r = run(["doc.pdf", "--white-bars", "-o", "white.pdf"])
    out_wb = os.path.join(WORK, "white.pdf")
    check(r.returncode == 0 and os.path.exists(out_wb), "--white-bars обрабатывает PDF",
          r.stderr.strip()[:120])
    if os.path.exists(out_wb):
        text, _, _ = pdf_text(out_wb)
        flat = norm_text(text)
        left = [b for b in BANNED_DOC if b in flat]
        check(not left, "данные скрыты и с белыми плашками", f"осталось: {left}")

    failed = [name for ok, name, _ in results if not ok]
    print(f"\nИтог: {len(results) - len(failed)} из {len(results)} проверок пройдено")
    if failed:
        print("Проблемы:")
        for name in failed:
            print("  -", name)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
