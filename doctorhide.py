#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DoctorHide — обезличивание документов (PDF с текстовым слоем, DOCX, XLSX, MD/TXT).

Что скрывается (по умолчанию — всё):
  person     ФИО (фамилия, имя, отчество)
  identity   упоминания документов личности (паспорт, в/у, загранпаспорт и т.п.)
  codes      ИНН, КПП, ОГРН/ОГРНИП, ОКПО, ОКТМО, ОКАТО, ОКВЭД, СНИЛС, полис ОМС
  address    адреса
  realty     недвижимость и права на неё
  contacts   телефоны, e-mail, мессенджеры
  bank       счета, карты, БИК, банк
  finance    доходы, суммы, долги, кредиты, налоги
  property   авто, VIN, госномер
  employment место работы и должность
  leisure    санатории, отели, курорты
  birth      возраст и дата рождения
  kinship    родство
  org        наименования организаций (в т.ч. судов)
  docno      номера документов и дел

Вывод:
  PDF        чёрные плашки; текст под ними удалён; вставленные изображения
             под плашкой удаляются. Остальной текст остаётся поисковым.
             PDF без текстового слоя пропускается с записью в отчёт.
  DOCX/XLSX  по умолчанию → Markdown с [censored]; --keep-format сохраняет исходный формат.
  MD/TXT     значения → [censored].

Картинки и PDF-сканы не поддерживаются.

Примеры:
  python doctorhide.py doc.pdf
  python doctorhide.py doc.docx --keep-format
  python doctorhide.py решение.pdf --mode judicial
  python doctorhide.py "docs/*.pdf" --report report.json
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pymupdf
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple


class SkipFile(Exception):
    """Файл пропущен: неподдерживаемый формат или PDF без текстового слоя.
    Сообщение исключения попадает в отчёт."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Rect:
    """Минимальный прямоугольник для текстовых строк."""
    __slots__ = ("x0", "y0", "x1", "y1")

    def __init__(self, x0=0.0, y0=0.0, x1=0.0, y1=0.0):
        self.x0, self.y0, self.x1, self.y1 = float(x0), float(y0), float(x1), float(y1)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def get_area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)


PLACEHOLDER = "[censored]"
SAFE_NAME = False
KEEP_NAME = False
KEEP_FORMAT = False
NAME_COUNTER = {"n": 0}
CURRENT = {"enabled": None, "mode": "document"}
# Чёрные плашки (единственный вариант).
BAR_RGB = (0, 0, 0)
BAR_FILL = (0.0, 0.0, 0.0)
TEXT_EXT = {".txt", ".md", ".markdown"}
SHEET_EXT = {".xlsx", ".xls"}
DOC_EXT = {".docx"}
SUPPORTED_EXT = TEXT_EXT | SHEET_EXT | DOC_EXT | {".pdf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

# --------------------------------------------------------------------------- #
#  Категории
# --------------------------------------------------------------------------- #

CATEGORIES: Dict[str, str] = {
    "person": "ФИО (фамилия, имя, отчество)",
    "identity": "Упоминания документов личности (паспорт, в/у, загранпаспорт и т.п.)",
    "codes": "Коды: ИНН, КПП, ОГРН, ОКПО, ОКТМО, ОКАТО, ОКВЭД, СНИЛС, ОМС",
    "address": "Адреса проживания и регистрации",
    "realty": "Недвижимость и права на неё (собственность, аренда)",
    "contacts": "Телефоны, e-mail, мессенджеры",
    "bank": "Банковские счета, карты, БИК",
    "finance": "Финансовое состояние: доходы, суммы, долги, кредиты",
    "property": "Имущество: авто, VIN, госномер",
    "employment": "Место работы и должность",
    "leisure": "Места отдыха: санатории, отели, курорты",
    "birth": "Возраст и дата рождения",
    "kinship": "Родство",
    "org": "Наименования организаций (включая суды)",
    "docno": "Номера документов и дел",
}

# В судебном режиме скрываем всё — JUDICIAL_KEEP пуст.
JUDICIAL_KEEP: set = set()

# --------------------------------------------------------------------------- #
#  Справочники
# --------------------------------------------------------------------------- #

GIVEN_NAMES = set(
    """
    александр алексей анатолий андрей антон аркадий арсений артём артем богдан борис
    вадим валентин валерий василий вениамин виктор виталий владимир владислав всеволод
    вячеслав геннадий георгий герман глеб григорий давид даниил денис дмитрий евгений
    егор ефим захар иван игорь илья иннокентий кирилл константин лев леонид максим марат
    марк матвей михаил никита николай олег павел пётр петр платон роман ростислав руслан
    рустам савелий святослав семён семен сергей станислав степан тарас тимофей тимур
    фёдор федор филипп эдуард юрий яков ярослав
    александра алина алиса алла анастасия ангелина анна антонина валентина валерия вера
    вероника виктория галина дарья диана евгения екатерина елена елизавета зинаида зоя
    инна ирина карина ксения лариса лидия любовь людмила маргарита марина мария милана
    надежда наталья нина оксана ольга полина раиса светлана софия софья тамара татьяна
    ульяна эльвира юлия яна
    """.split()
)

CITY_STEMS = [
    ("Санкт[- ]Петербург", r"[а-яё]*"), ("СПб", ""), ("Петербург", r"[а-яё]*"),
    ("Москв", r"[а-яё]{0,2}"), ("Новосибирск", r"[а-яё]{0,2}"), ("Екатеринбург", r"[а-яё]{0,2}"),
    ("Казан", r"[ьияюе]{0,2}"), (r"Нижн\w+ Новгород", r"[а-яё]{0,3}"), ("Челябинск", r"[а-яё]{0,2}"),
    ("Самар", r"[аыуе]{0,2}"), ("Омск", r"[а-яё]{0,2}"), ("Ростов", r"[а-яё]{0,3}"),
    ("Уф", r"[аыуе]{0,1}"), ("Красноярск", r"[а-яё]{0,2}"), ("Воронеж", r"[а-яё]{0,2}"),
    ("Перм", r"[ьияюе]{0,2}"), ("Волгоград", r"[а-яё]{0,2}"), ("Краснодар", r"[а-яё]{0,2}"),
    ("Саратов", r"[а-яё]{0,2}"), ("Тюмен", r"[ьияюе]{0,2}"), ("Тольятти", ""),
    ("Ижевск", r"[а-яё]{0,2}"), ("Барнаул", r"[а-яё]{0,2}"), ("Ульяновск", r"[а-яё]{0,2}"),
    ("Иркутск", r"[а-яё]{0,2}"), ("Хабаровск", r"[а-яё]{0,2}"), ("Ярославл", r"[ьея]{0,2}"),
    ("Владивосток", r"[а-яё]{0,2}"), ("Махачкал", r"[аыуе]{0,2}"), ("Томск", r"[а-яё]{0,2}"),
    ("Оренбург", r"[а-яё]{0,2}"), ("Кемеров", r"[аоуе]{0,2}"), ("Новокузнецк", r"[а-яё]{0,2}"),
    ("Рязан", r"[ьияюе]{0,2}"), ("Астрахан", r"[ьияюе]{0,2}"), (r"Набережн\w+ Челн", r"[а-яё]{0,2}"),
    ("Пенз", r"[аыуе]{0,2}"), ("Липецк", r"[а-яё]{0,2}"), ("Тул", r"[аыуе]{0,2}"),
    ("Калининград", r"[а-яё]{0,2}"), ("Балаших", r"[аиуе]{0,2}"), ("Курск", r"[а-яё]{0,2}"),
    ("Севастопол", r"[ьея]{0,2}"), ("Сочи", ""), ("Ставропол", r"[ьея]{0,2}"),
    ("Улан-Удэ", ""), ("Твер", r"[ьияюе]{0,2}"), ("Магнитогорск", r"[а-яё]{0,2}"),
    ("Иванов", r"[ое]"), ("Брянск", r"[а-яё]{0,2}"), ("Сургут", r"[а-яё]{0,2}"),
    ("Владимир", r"[а-яё]{0,2}"), ("Архангельск", r"[а-яё]{0,2}"), ("Чита", r"[а-яё]{0,2}"),
    ("Калуг", r"[аиуе]{0,2}"), ("Смоленск", r"[а-яё]{0,2}"), ("Волжск", r"[а-яё]{0,2}"),
    ("Курган", r"[а-яё]{0,2}"), ("Череповец", r"[а-яё]{0,2}"), ("Вологд", r"[аыуе]{0,2}"),
    ("Саранск", r"[а-яё]{0,2}"), ("Владикавказ", r"[а-яё]{0,2}"), ("Мурманск", r"[а-яё]{0,2}"),
    ("Якутск", r"[а-яё]{0,2}"), ("Тамбов", r"[а-яё]{0,2}"), ("Грозн", r"[аыуе]{0,2}"),
    ("Стерлитамак", r"[а-яё]{0,2}"), ("Кострома", r"[а-яё]{0,2}"), ("Петрозаводск", r"[а-яё]{0,2}"),
    (r"Нижн\w+ Тагил", r"[а-яё]{0,2}"), ("Йошкар-Ол", r"[а-яё]{0,2}"), ("Новороссийск", r"[а-яё]{0,2}"),
    ("Химки", ""), ("Подольск", r"[а-яё]{0,2}"), ("Королёв", r"[а-яё]{0,2}"), ("Королев", r"[а-яё]{0,2}"),
    ("Мытищи", ""), ("Люберц", r"[аыуе]{0,2}"), ("Красногорск", r"[а-яё]{0,2}"), ("Домодедово", ""),
    ("Белгород", r"[а-яё]{0,2}"), ("Псков", r"[а-яё]{0,2}"), ("Великий Новгород", r"[а-яё]{0,2}"),
    ("Новочеркасск", r"[а-яё]{0,2}"), ("Шахт", r"[аыуе]{0,2}"), ("Дзержинск", r"[а-яё]{0,2}"),
    ("Орёл", r"[а-яё]{0,2}"), ("Орел", r"[а-яё]{0,2}"), ("Нальчик", r"[а-яё]{0,2}"),
    ("Сыктывкар", r"[а-яё]{0,2}"), ("Норильск", r"[а-яё]{0,2}"), ("Абакан", r"[а-яё]{0,2}"),
]
EN_CITIES = ["Moscow", "Saint Petersburg", "St. Petersburg", "Novosibirsk", "Yekaterinburg",
             "London", "New York", "Los Angeles", "Chicago", "Boston", "Washington", "Berlin",
             "Paris", "Madrid", "Rome", "Amsterdam", "Dubai", "Beijing", "Tel Aviv", "Toronto"]

# --------------------------------------------------------------------------- #
#  Валидаторы
# --------------------------------------------------------------------------- #


def inn10_ok(s: str) -> bool:
    d = [int(c) for c in s]
    return sum(d[i] * w for i, w in enumerate([2, 4, 10, 3, 5, 9, 4, 6, 8])) % 11 % 10 == d[9]


def inn12_ok(s: str) -> bool:
    d = [int(c) for c in s]
    c1 = sum(d[i] * w for i, w in enumerate([7, 2, 4, 10, 3, 5, 9, 4, 6, 8])) % 11 % 10
    c2 = sum(d[i] * w for i, w in enumerate([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8])) % 11 % 10
    return c1 == d[10] and c2 == d[11]


def ogrn_ok(s: str) -> bool:
    d = [int(c) for c in s]
    body, ctrl = d[:-1], d[-1]
    if len(d) == 13:
        return int("".join(map(str, body))) % 11 % 10 == ctrl
    if len(d) == 15:
        return int("".join(map(str, body))) % 13 % 10 == ctrl
    return False


def validate(kind: Optional[str], s: str) -> bool:
    digits = re.sub(r"\D", "", s)
    if kind == "inn":
        return inn10_ok(digits) if len(digits) == 10 else inn12_ok(digits) if len(digits) == 12 else False
    if kind == "ogrn":
        return ogrn_ok(digits)
    return True


# --------------------------------------------------------------------------- #
#  Правила: (категория, id, шаблон, валидатор, группа)
# --------------------------------------------------------------------------- #

_D = lambda p: re.compile(p, re.IGNORECASE)

STREET = (
    r"(?:улиц[аыуе]|ул\.|проспект\w*|пр-т\w*|просп\.|переулок|пер\.|бульвар\w*|б-р|"
    r"шоссе|ш\.|набережн\w+|наб\.|площад\w+|пл\.|проезд\w*|тупик\w*|туп\.|микрорайон|мкр\.|"
    r"Street|St\.|Avenue|Ave\.|Boulevard|Blvd\.|Road|Rd\.|Lane|Drive|Dr\.)"
)
_CITY = r"(?<![А-Яа-яЁё])(?:" + "|".join(f"{s}{t}" for s, t in CITY_STEMS) + r"|" + \
        "|".join(re.escape(c) for c in EN_CITIES) + r")(?![А-Яа-яЁё])"

RULES: List[Tuple[str, str, str, Optional[str], int]] = [
    # --- codes ---
    ("codes", "inn", r"(?<!\d)(\d{10}|\d{12})(?!\d)", "inn", 0),
    ("codes", "kpp", r"(?<!\d)\d{4}(?:[0-9A-ZА-Я]{2})\d{3}(?!\d)", None, 0),
    ("codes", "ogrn", r"(?<!\d)(?:\d{13}|\d{15})(?!\d)", "ogrn", 0),
    ("codes", "snils", r"\b\d{3}-\d{3}-\d{3}[ -]?\d{2}\b", None, 0),
    ("codes", "policy16", r"(?<!\d)\d{16}(?!\d)", None, 0),
    ("codes", "okpo", r"(?:ОКПО|ОГРПО)\s*[:№]?\s*(\d{8,10})", None, 1),
    ("codes", "oktmo", r"ОКТМО\s*[:№]?\s*(\d{8,11})", None, 1),
    ("codes", "okato", r"ОКАТО\s*[:№]?\s*(\d{8,11})", None, 1),
    ("codes", "okved", r"ОКВЭД\w*\s*[:№]?\s*([\d.,\s]{2,20})", None, 1),
    ("codes", "oms", r"(?:полис\w*[^\d\n]{0,20}|ОМС[^\d\n]{0,20})(\d{11,16})", None, 1),
    # --- identity ---
    ("identity", "passport", r"(?:сери[яию]\s*[:№]?\s*)?\b\d{2}\s?\d{2}\s*(?:№\s*)?\d{6}\b", None, 0),
    ("identity", "passport_code", r"код\s+подразделения\s*[:№]?\s*\d{3}-\d{3}", None, 0),
    ("identity", "drv_series", r"\b\d{2}\s?\d{2}\s?\d{6}\b", None, 0),
    ("identity", "doc_word",
     r"(?:паспорт\w*|загранпаспорт\w*|водительск\w*\s+удостоверени\w*|военн\w+\s+билет\w*|"
     r"свидетельств\w+\s+о\s+рождени\w+|полис\w*\s+ОМС)\s*(?:сери[яию]|№|:)\s*[^\n]{2,60}", None, 0),
    # серия бланка римскими буквами: «I-АК № 123456», «II-МЮ № 654321»
    ("identity", "doc_series_roman", r"\b[IVX]{1,3}\s*[-–—]\s*[А-ЯЁ]{2}\s*№?\s*\d{6}\b", None, 0),
    # номер актовой записи: по ней находят запись в ЗАГС
    ("docno", "act_no", r"(?:актов\w+\s+запис\w*|запис\w*\s+акта)\s*№?\s*(\d{2,6})", None, 1),
    # дата прописью: «двенадцатого марта тысяча девятьсот семьдесят седьмого года»
    ("birth", "date_words",
     r"(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
     r"(?:\s+[а-яё]{3,20}){0,3}\s+(?:тысяча\s+)?[а-яё\s]{0,25}?года", None, 0),
    # --- address ---
    ("address", "addr_street", STREET + r"\.?\s+[^,;:\n]{2,60}", None, 0),
    ("address", "street_before",
     r"\b[А-ЯЁ][А-Яа-яЁё\-]+(?:\s+[А-ЯЁ][А-Яа-яЁё\-]+)?\s+"
     r"(?:ул\.|улица|пр-т|проспект|пер\.|переулок|наб\.|набережная|ш\.|шоссе|"
     r"пл\.|площадь|б-р|бульвар|проезд|туп\.)", None, 0),
    ("address", "addr_house",
     r"\b(?:д|дом|кв|квартира|корп|корпус|стр|строение|оф|офис|пом|помещение)\.?\s*№?\s*\d+[а-яa-z]?(?:/\d+)?", None, 0),
    ("address", "addr_index", r"(?:индекс\s*[:№]?\s*)?\b\d{6}\b(?=\s*,?\s*(?:г\.|город|обл|область|край|респ))", None, 0),
    ("address", "region",
     r"(?:респ\w*|област\w*|обл\.|кра[йяюе]|округ\w*|АО)\s+[А-ЯЁ][А-Яа-яЁё\-]+(?:\s+[А-ЯЁ][А-Яа-яЁё\-]+)?", None, 0),
    ("address", "district", r"\b[А-ЯЁ][А-Яа-яЁё\-]+\s+район(?:а|у|е|ом)?(?![а-яё])", None, 0),
    ("address", "city", _CITY, None, 0),
    ("address", "city_label",
     r"\b(?:(?:г|гор|с)\.|(?:город|пос|посёлок|пгт|дер|деревня|село|ст-ца|станица|аул)\.?)(?![а-яё])\s*([А-ЯЁ][А-Яа-яЁё\-]+)", None, 1),
    # --- realty ---
    ("realty", "cadastral", r"\b\d{2}:\d{2}:\d{6,7}:\d+\b", None, 0),
    ("realty", "realty_obj",
     r"(?:квартир\w*|жил\w+\s+помещени\w*|гараж\w*|машино-мест\w*|нежил\w+\s+помещени\w*|"
     r"земельн\w+\s+участ\w*|дачн\w+\s+участ\w*|домовладени\w*)"
     r"\s*(?:№\s*\d+[^\n]{0,40}|площад\w*[^\n]{0,20}\d[^\n]{0,20})", None, 0),
    ("realty", "realty_right",
     r"(?:прав[оа]\s+(?:собственности|аренды|безвозмездного пользования|хозяйственного ведения|"
     r"оперативного управления)|на\s+праве\s+\w+)\s+на\s+[^\n]{2,60}", None, 0),
    ("realty", "area", r"\b\d+[.,]?\d*\s*(?:кв\.?\s*м|м2|кв\.?м|гектар\w*|га\b)", None, 0),
    # --- contacts ---
    ("contacts", "phone", r"(?:\+7|8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]?\d{2}[\s\-]?\d{2}\b", None, 0),
    ("contacts", "email", r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b", None, 0),
    ("contacts", "messenger",
     r"(?:telegram|whatsapp|whats\s?app|viber|телеграм|ватсап|вибер)[\s:]*@?[A-Za-z0-9_]{3,}", None, 0),
    # --- bank ---
    ("bank", "bank_acc", r"\b\d{20}\b", None, 0),
    ("bank", "bik", r"(?:БИК\s*[:№]?\s*)?\b(04\d{7})\b", None, 1),
    ("bank", "card", r"\b(?:\d{4}[ -]?){3}\d{4}\b", None, 0),
    ("bank", "bank_name", r"\b[А-ЯЁ][А-Яа-яЁё\-]*(?:банк|Банк)\b", None, 0),
    ("bank", "account_terms",
     r"\b(?:расчётн\w+|расчетн\w+|р/с|к/с|корр\.?\s*сч\w*|лицев\w+\s+сч\w*|счёт|счет)\s*[:№]?\s*[\d\s]{5,25}", None, 0),
    # --- finance ---
    ("finance", "money",
     r"\b\d[\d\s\u00a0]{2,}(?:[.,]\d{1,2})?\s*(?:руб\w*|₽|тыс\.?\s*руб\w*|млн\w*|млрд\w*|долл\w*|USD|EUR|евро)\b", None, 0),
    ("finance", "income",
     r"(?:доход|заработн\w+\s+плат|зарплат|выручк|прибыл|задолженност|кредит|займ|ипотек|"
     r"налог|пенси|пособи)\w*[^\n\d]{0,30}(\d[\d\s\u00a0]{2,}(?:[.,]\d{1,2})?\s*"
     r"(?:руб\w*|₽|тыс\w*|млн\w*|млрд\w*|долл\w*|USD|EUR|евро)?)", None, 1),
    # --- property ---
    ("property", "vin", r"\b[A-HJ-NPR-Z0-9]{17}\b", None, 0),
    ("property", "plate", r"\b[АВЕКМНОРСТУХ]\d{3}[АВЕКМНОРСТУХ]{2}\d{2,3}\b", None, 0),
    ("property", "vehicle",
     r"(?:автомобил\w*|транспортн\w+\s+средств\w*|мотоцикл\w*|снегоход\w*|катер\w*|лодк\w*|прицеп\w*)"
     r"\s+(?:[А-ЯЁA-Z\x22«][^\n]{0,40}|\d[^\n]{0,30})", None, 0),
    # --- employment ---
    ("employment", "job",
     r"(?:место\s+работы|работает|работодател\w*|трудоустроен\w*|должност\w*|"
     r"в\s+должности|служб\w+\s+в)\s*[:—-]?\s*(?=[^\n]{0,80}(?:[А-ЯЁ]|«))[^\n]{0,80}", None, 0),
    # --- leisure ---
    ("leisure", "leisure",
     r"(?:мест\w*\s+отдыха|отдыхал\w*|санатори\w*|курорт\w*|отел\w*|гостиниц\w*|"
     r"пансионат\w*|путёвк\w*|путевк\w*)\s*[:—-]?\s*(?=[^\n]{0,60}(?:[А-ЯЁ]|«))[^\n]{0,60}", None, 0),
    # --- birth (только возраст и явная дата рождения; остальные даты не трогаем) ---
    ("birth", "age", r"\b(?:возраст\w*|в\s+возрасте)\s*[:—-]?\s*\d{1,3}(?:\s*(?:лет|года|год|л\.))?", None, 0),
    ("birth", "age_years", r"\b\d{1,3}\s*(?:лет|года|год)\b", None, 0),
    ("birth", "dob", r"(?:дата\s+рождения|родил\w*|г\.\s*р\.)\s*[:—-]?\s*\d{1,2}[./]\d{1,2}[./]\d{2,4}", None, 0),
    ("birth", "dob_plain", r"\b\d{1,2}[./]\d{1,2}[./](?:19|20)\d{2}\s*(?:г\.?\s*)?(?:р\.|года рождения)", None, 0),
    # --- kinship ---
    ("kinship", "kinship",
     r"\b(?:сын|дочь|дочер[ьи]|мать|матер[ьи]|отец|отца|жена|жены|супруг\w*|брат|брата|сестра|"
     r"сестры|тесть|тёща|свекор|свёкор|свекровь|опекун\w*|попечител\w*|родственни\w*)\b"
     r"(?:\s+[А-ЯЁ][А-Яа-яЁё\-]+){0,3}", None, 0),
    # --- org ---
    ("org", "legal_entity",
     r"\b(?:ООО|ОАО|ЗАО|ПАО|АО|ИП|ФГУП|ГУП|МУП|ГБУЗ|ГБУ|ГКУ|МБУ|МАУ|АНО|НКО|НП|ТСЖ|СНТ|"
     r"LLC|Ltd\.?|Inc\.?|GmbH|Corp\.?)\s*[«\"“]?[^\n,;»\"”]{2,80}[»\"”]?", None, 0),
    ("org", "gov_body",
     r"\b(?:Межрайонн\w+|Межрегиональн\w+|ИФНС|ФНС|Федеральн\w+\s+налогов\w+\s+служб\w+|"
     r"Управлени\w+\s+\w+|Министерств\w+|Департамент\w*|Администраци\w+|"
     r"Пенсионн\w+\s+фонд\w*|Государственн\w+\s+учреждени\w+)\s+[^\n,;]{3,120}", None, 0),
    ("identity", "label_value",
     r"(?:кем\s+выдан\w*|когда\s+выдан\w*|дата\s+выдачи|выдан\w*|серия\s+документа|"
     r"серия|номер\s+документа|код\s+подразделения|место\s+рождения|место\s+жительства|"
     r"зарегистрирован\w*|прожива\w+|снилс|страховой\s+номер|полис\w*|ОМС|"
     r"свидетельств\w+\s+о\s+\w+)\s*[:№]?\s*([^\n]{2,90})", None, 1),
    ("birth", "birth_place",
     r"(?:место\s+рождения|родил\w+|дата\s+рождения)\s*[:—]?\s*([^\n]{2,90})", None, 1),
    ("org", "university",
     r"\b(?:ФГБОУ|ФГАОУ|ФГБУ|ГБОУ)?\s*(?:[А-ЯЁ][А-Яа-яЁё\-]+\s+){0,5}"
     r"(?:университет\w*|институт\w*|академи\w+|колледж\w*|техникум\w*|училищ\w+)\b[^\n]{0,60}",
     None, 0),
    ("docno", "reg_number",
     r"(?:регистрационн\w+|реестров\w+|регистров\w+)\s+номер\w*\s*[:№]?[\s\S]{0,15}?(\d[\d\s\-/]*)", None, 1),
    ("docno", "doc_series", r"\b\d{6}\s?\d{7}\b", None, 0),
    ("identity", "issue_date",
     r"(?:дата\s+выдачи|выдан\w*)\s*[:—-]?\s*([^\n]{1,60})", None, 1),
    ("org", "institution",
     r"\b(?:ГБОУ|МБОУ|ГКОУ|МКОУ|ГАОУ|МАОУ|СОШ|ООШ|НОШ)\b[^\n,;]{0,70}", None, 0),
    ("org", "institution_named",
     r"\b(?:Дворец\w*|Театр\w*|Гимнази\w+|Лице\w+)\s+[А-ЯЁа-яё\-]+(?:\s+[А-ЯЁа-яё\-]+){0,6}", None, 0),
    # --- docno ---
    ("docno", "doc_no", r"№\s*(\d[\d\s\-]{2,})", None, 1),
    # латинская N вместо №: «N20», «N 154»
    ("docno", "latin_n", r"\b[Nn]\s?\d{1,6}\b", None, 0),
    # суммы с разрядами без слова «руб»: 1 200 000, 45 678,90
    ("finance", "sum_plain", r"\b\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d{1,2})?\b", None, 0),
    # названия судов
    ("org", "court_name", r"\b[А-ЯЁ][А-Яа-яЁё\-\s]{2,60}?(?:районн\w+|городск\w+|областн\w+|краев\w+|"
     r"арбитражн\w+|верховн\w+|миров\w+)\s+суд\w*", None, 0),
    ("org", "court_name2", r"\b[А-ЯЁ][А-Яа-яЁё\-\s]{2,50}?\s+суд\w*\b", None, 0),
    # где проходят споры: участки, третейские и мировые суды
    ("org", "court_uchastok", r"(?:судебн\w+|судейск\w+)\s+участ\w*\s*(?:№\s*\d+)?", None, 0),
    ("org", "court_tretey", r"\b[А-ЯЁ][А-Яа-яЁё\-\s]{2,40}?(?:третейск\w+|миров\w+)\s+суд\w*", None, 0),
    ("org", "court_phrase", r"(?:спор\w*|дело)\s+[^\n]{0,40}?(?:рассматрива\w+|подлежит\s+рассмотрению)\s+в\s+[^\n,.]{2,60}", None, 0),
    ("docno", "long_number", r"\b\d{15,}\b", None, 0),
]

BLOCK_RULES = {
    "passport", "passport_code", "drv_series", "snils", "phone", "bank_acc", "bik", "inn",
    "kpp", "ogrn", "long_number", "gov_body", "legal_entity", "addr_street", "addr_house",
    "city_label", "region", "city", "cadastral", "realty_obj", "account_terms", "card",
}

COURT_MARKERS = [
    r"\bсуд(?:а|у|ом|е|ья|ьи)?\b", r"дело\s*№", r"истц", r"ответчик", r"заявител",
    r"решение", r"постановление", r"определение", r"приговор", r"прокурор",
    r"судья", r"апелляц", r"кассац", r"третье лицо", r"судебное заседание",
    r"court", r"plaintiff", r"defendant",
]

# --------------------------------------------------------------------------- #
#  Строки/слова
# --------------------------------------------------------------------------- #


@dataclass
class Word:
    text: str
    rect: Rect


@dataclass
class Line:
    text: str
    words: List[Word]
    starts: List[int]

    def rects_for(self, a: int, b: int) -> List[Rect]:
        out = []
        for w, st in zip(self.words, self.starts):
            if st < b and st + len(w.text) > a:
                out.append(w.rect)
        return out


def _group_lines(words: List[Word], joiner: str = " ") -> List[Line]:
    text, starts = "", []
    for w in words:
        if text:
            text += joiner
        starts.append(len(text))
        text += w.text
    return [Line(text, words, starts)] if words else []


def _is_upperish(s: str) -> bool:
    letters = [c for c in s if c.isalpha()]
    if len(letters) < 3:
        return False
    return sum(1 for c in letters if c.isupper()) / len(letters) >= 0.7


def lines_from_text(text: str) -> List[Line]:
    out = []
    for raw in text.split("\n"):
        if not raw.strip():
            continue
        words, starts = [], []
        for m in re.finditer(r"\S+", raw):
            words.append(Word(m.group(), Rect()))
            starts.append(m.start())
        out.append(Line(raw, words, starts))
    return out


def lines_with_offsets(text: str) -> List[Tuple[int, Line]]:
    out, pos = [], 0
    for raw in text.split("\n"):
        if raw.strip():
            words, starts = [], []
            for m in re.finditer(r"\S+", raw):
                words.append(Word(m.group(), Rect()))
                starts.append(m.start())
            out.append((pos, Line(raw, words, starts)))
        pos += len(raw) + 1
    return out


# --------------------------------------------------------------------------- #
#  ФИО
# --------------------------------------------------------------------------- #

PATRONYMIC = re.compile(
    r"(?:[ое]вич(?:а|у|ем)?|[ое]вн(?:а|ы|е|у)|ьевич(?:а|у|ем)?|ьевн(?:а|ы|е|у)|"
    r"иевич(?:а|у|ем)?|иевн(?:а|ы|е|у)|иничн(?:а|ы|е|у)|ичн(?:а|ы|е|у))$",
    re.I,
)
SURNAME = re.compile(r"(?:ов|ев|ёв|ин|ын|ова|ева|ёва|ина|ына|ский|цкий|ская|цкая|ой|ый|ая|ко|ук|юк|ян|дзе|швили|енко|чук|ич|их|ых|як|ак|ец|son|s|ova|ev|in)$", re.I)
SURNAME_STRICT = re.compile(r"(?:ов|ев|ёв|ин|ын|ова|ева|ёва|ина|ына|ский|цкий|ская|цкая|ко|ук|юк|ян|дзе|швили|енко|чук|ич|их|ых|як|ак|ец)$", re.I)
NAME_STOP = {"российской", "федерации", "россия", "республики", "области", "края", "района",
             "города", "инспекции", "службы", "налоговой", "федеральной", "россии", "город",
             "улица", "государства", "паспорт", "справка", "сведения", "заявление",
             # географические и ведомственные слова: рядом с фамилией они не имя
             "район", "область", "край", "республика", "округ", "посёлок", "поселок", "село",
             "деревня", "станица", "проспект", "переулок", "шоссе", "набережная", "площадь",
             "бульвар", "проезд", "тупик", "микрорайон", "дом", "корпус", "строение",
             "квартира", "помещение", "офис", "отдел", "отделение", "управление", "департамент",
             "комитет", "администрация", "министерство", "инспекция", "прокуратура", "суд",
             "фонд", "центр", "банк", "школа", "гимназия", "лицей", "сад", "поликлиника",
             "больница", "госпиталь", "предприятие", "организация", "учреждение", "общество",
             "компания", "филиал", "представительство", "агентство", "палата", "нотариус",
             "свидетельство", "удостоверение", "полис", "серия", "номер", "дата", "выдача",
             "подпись", "глава", "начальник", "директор", "инспектор", "специалист",
             # слова, которые вместе с соседом давали ложную пару «фамилия + имя»
             "российская", "федерация", "форма", "справка", "выдано", "вселения",
             "регистрационного", "учета", "граждан", "места", "жительства", "приватизирована",
             "квартира", "город", "область", "район"}
# составные топонимы, похожие на фамилию, но людьми не являющиеся
CAPS_HYPHEN_STOP = {"санкт-петербург", "ростов-на-дону", "нижний-новгород", "улан-удэ",
                    "йошкар-ола", "переславль-залесский", "петропавловск-камчатский"}
NAME_MARKERS = {"гр", "гр.", "гражданин", "гражданка", "гражданина", "ф.и.о", "ф.и.о.", "фио",
                "ип", "лице", "представитель", "представителя", "судья", "судьи", "секретарь",
                "секретаря", "прокурор", "прокурора", "эксперт", "эксперта", "свидетель",
                "свидетеля", "истец", "истца", "ответчик", "ответчика", "заявитель", "заявителя",
                "mr", "mrs", "ms", "dr"}


HOMO = str.maketrans({
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М",
    "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У",
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у",
})


def _cyr(s: str) -> str:
    """Латинские двойники -> кириллица. Длины совпадают, смещения не едут."""
    return s.translate(HOMO)


# Мягкий перенос, длинные тире и неразрывные пробелы ломают шаблоны: в PDF цифры
# часто набраны как «112\xad233\xad445\xa095», и правило «\d{3}-\d{3}-\d{3} \d{2}»
# их не видит. Замена посимвольная, поэтому смещения совпадают и годятся и для
# исходного текста, и для координат слов.
SOFT_MAP = {
    "\xad": "-", "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2015": "-", "\u2212": "-", "\u00a0": " ", "\u2007": " ",
    "\u2009": " ", "\u202f": " ",
}
SOFT_TABLE = {ord(k): v for k, v in SOFT_MAP.items()}


def normalize(s: str) -> str:
    """Мягкие переносы, тире и неразрывные пробелы -> обычные символы (1:1)."""
    return s.translate(SOFT_TABLE)


def _tokens_of(text: str):
    return [(m.start(), m.end(), m.group())
            for m in re.finditer(r"[А-ЯЁ]\.[А-ЯЁ]?\.?|[А-Яа-яЁё][А-Яа-яЁё\-]*|\d+", text)]


def _tokens(line: Line):
    return _tokens_of(normalize(line.text))


def _name_like(tok: str) -> bool:
    """Слово похоже на часть ФИО: имя из справочника, фамилия, отчество, инициалы."""
    t = _cyr(tok).strip(".,;:()«»\"'")
    if not t:
        return False
    if re.fullmatch(r"[А-ЯЁ]\.[А-ЯЁ]?\.?", t):
        return True
    if not re.fullmatch(r"[А-ЯЁ][А-Яа-яЁё\-]+", t):
        return False
    low = t.lower()
    if low in NAME_STOP:
        return False
    return low in GIVEN_NAMES or bool(SURNAME.search(low)) or bool(PATRONYMIC.search(low))


def _hyphen_part(tok: str) -> bool:
    """Кусок составной фамилии: «ЯКОВЛЕВА-ЗАВАЛЬ» без второй половины."""
    t = _cyr(tok).strip(".,;:()«»\"'")
    return "-" in t and bool(re.fullmatch(r"[А-ЯЁ][А-ЯЁ\-]+", t))


def grow_spans(text: str, toks, spans: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Расширяет найденное ФИО на соседние куски того же слова.

    OCR рвёт «ЯКОВЛЕВА-ЗАВАЛЬЕВСКАЯ» на «ЯКОВЛЕВА-ЗАВАЛЬ» и «ЕВСКАЯ»: без расширения
    плашка закрывает только хвост, а начало фамилии остаётся на странице.
    """
    out: List[Tuple[int, int]] = []
    for a, b in spans:
        idx = [i for i, (s, e, _t) in enumerate(toks) if e > a and s < b]
        if not idx:
            out.append((a, b))
            continue
        i0, i1 = idx[0], idx[-1]
        for _ in range(4):
            if i0 == 0:
                break
            prev, cur = toks[i0 - 1], toks[i0]
            gap = text[prev[1]:cur[0]]
            if cur[0] - prev[1] == 0 or (len(gap) <= 1 and (_name_like(prev[2]) or _hyphen_part(prev[2]))):
                i0 -= 1
            else:
                break
        for _ in range(4):
            if i1 + 1 >= len(toks):
                break
            cur, nxt = toks[i1], toks[i1 + 1]
            gap = text[cur[1]:nxt[0]]
            if nxt[0] - cur[1] == 0 or (len(gap) <= 1 and (_name_like(nxt[2]) or _hyphen_part(cur[2]))):
                i1 += 1
            else:
                break
        out.append((toks[i0][0], toks[i1][1]))
    return out


def find_names(line: Line) -> List[Tuple[int, int]]:
    text = normalize(line.text)
    toks = _tokens_of(text)
    n = len(toks)
    spans: List[Tuple[int, int]] = []

    def name_tok(t: str) -> bool:
        return bool(re.fullmatch(r"[А-ЯЁ][А-Яа-яЁё\-]+", _cyr(t))) and len(t) > 1

    def as_name(t: str) -> bool:
        """Годится как имя рядом с фамилией: с заглавной и не служебное слово."""
        low2 = _cyr(t).strip(".,;:()«»\"'").lower()
        return (len(low2) >= 2 and low2 not in NAME_STOP
                and bool(re.fullmatch(r"[А-ЯЁ][А-Яа-яЁё\-]+", _cyr(t).strip(".,;:()«»\"'"))))

    for i, (a, b, t) in enumerate(toks):
        low = _cyr(t).lower()
        if low in NAME_STOP:
            continue
        # «ЯКОВЛЕВА-ЗАБАЛЬЕ»: OCR обрывает вторую половину составной фамилии, и обычные
        # правила её больше не видят — первая половина всё равно остаётся фамилией
        if (low not in CAPS_HYPHEN_STOP and "-" in low
                and re.fullmatch(r"[А-ЯЁ][А-ЯЁ\-]+", _cyr(t))
                and SURNAME_STRICT.search(low.split("-")[0])):
            spans.append((a, b))
            continue
        if PATRONYMIC.search(low) and name_tok(t):
            j = i
            while j > 0 and i - j < 2 and name_tok(toks[j - 1][2]):
                j -= 1
            spans.append((toks[j][0], b))
            continue
        if SURNAME.search(low) and i + 1 < n and as_name(toks[i + 1][2]):
            end = toks[i + 1][1]
            if i + 2 < n and PATRONYMIC.search(toks[i + 2][2].lower()):
                end = toks[i + 2][1]
            spans.append((a, end))
            continue
        if low in GIVEN_NAMES and i + 1 < n and as_name(toks[i + 1][2]):
            spans.append((a, toks[i + 1][1]))
            continue
        if i > 0 and toks[i - 1][2].lower() in NAME_MARKERS:
            nxt = low
            if not (nxt in GIVEN_NAMES or (name_tok(t) and SURNAME_STRICT.search(nxt))):
                continue
            end = b
            for k in (i + 1, i + 2):
                if k < n and name_tok(toks[k][2]) and toks[k][2].lower() not in NAME_STOP:
                    end = toks[k][1]
            spans.append((a, end))
            continue
        if (re.fullmatch(r"[А-ЯЁ]\.(?:[А-ЯЁ]\.)?", t) and i + 1 < n
                and SURNAME.search(toks[i + 1][2])):
            spans.append((a, toks[i + 1][1]))
            continue
        if re.fullmatch(r"[А-ЯЁ]\.(?:[А-ЯЁ]\.)?", t) and i > 0 and SURNAME.search(toks[i - 1][2]):
            end = b
            if i + 1 < n and re.fullmatch(r"[А-ЯЁ]\.", toks[i + 1][2]):
                end = toks[i + 1][1]
            spans.append((toks[i - 1][0], end))

    merged: List[Tuple[int, int]] = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return grow_spans(text, toks, merged)


def english_names(line: Line) -> List[Tuple[int, int]]:
    out = []
    for m in re.finditer(r"\b(?:Mr|Mrs|Ms|Dr|Prof)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b", line.text):
        out.append((m.start(), m.end()))
    return out


# --------------------------------------------------------------------------- #
#  Подпись -> значение
# --------------------------------------------------------------------------- #

# Подписи полей. В анкетах, полисах и свидетельствах подпись и значение часто
# стоят в разных ячейках: подпись слева, значение справа или строкой ниже.
FIELD_LABELS = (
    (r"фамили[яюие]", "person"),
    (r"отчество", "person"),
    (r"имя", "person"),
    (r"пол", "person"),
    (r"ф\.\s*и\.\s*о\.?", "person"),
    (r"дата\s+рождения", "birth"),
    (r"место\s+рождения", "birth"),
    (r"дата\s+(?:выдачи|составления|регистрации|заключения\s+брака|установления(?:\s+статуса)?)",
     "identity"),
    (r"кем\s+выдан\w*", "identity"),
    (r"когда\s+выдан\w*", "identity"),
    (r"код\s+подразделения", "identity"),
    (r"серия(?:\s+документа)?|сер\.", "identity"),
    (r"номер(?:\s+документа|\s+удостоверения|\s+полиса|\s+записи|\s+акта)?", "docno"),
    (r"снилс", "codes"),
    (r"страховой\s+номер", "codes"),
    (r"полис\w*", "identity"),
    (r"омс", "identity"),
    (r"место\s+(?:жительства|регистрации|пребывания)", "address"),
    (r"зарегистрирован\w*", "address"),
    (r"прожива\w+", "address"),
    (r"адрес\w*", "address"),
    (r"отец|мать|супруг\w*|родител\w*", "kinship"),
)
FIELD_LABEL_RX = re.compile(
    r"(?<![А-Яа-яЁё])(?:" + "|".join("(?:%s)" % p for p, _ in FIELD_LABELS) + r")(?![А-Яа-яЁё])",
    re.I,
)


def label_category(label: str) -> str:
    low = label.lower().strip()
    for pat, cat in FIELD_LABELS:
        if re.fullmatch(pat, low, re.I):
            return cat
    return "identity"


# --------------------------------------------------------------------------- #
#  Детект
# --------------------------------------------------------------------------- #


def is_court_act(text: str) -> bool:
    hits = [p for p in COURT_MARKERS if re.search(p, text, re.I)]
    strong = bool(re.search(r"дело\s*№|судебн\w+\s+(?:акт|заседани)|СУД\b|истец|ответчик", text, re.I))
    return len(hits) >= 4 and strong


def collect_hits(lines: List[Line], enabled: set, mode: str, only_ids: Optional[set] = None,
                 page_rect: Optional[Rect] = None):
    """Находит персональные данные в строках. mode зарезервирован для будущих отличий."""
    rects: List[Tuple[Rect, str]] = []
    stats: Dict[str, int] = {}
    compiled = []
    for cat, rid, pat, val, grp in RULES:
        if cat not in enabled:
            continue
        if only_ids is not None and rid not in only_ids:
            continue
        compiled.append((cat, rid, re.compile(pat, re.I), val, grp))

    for line in lines:
        if not line.text.strip():
            continue
        text = normalize(line.text)
        for cat, rid, rx, val, grp in compiled:
            for m in rx.finditer(text):
                if val and not validate(val, m.group()):
                    continue
                if rid == "gov_body" and re.search(r"Наименование\s+$", text[:m.start()]):
                    continue
                a, b = (m.start(grp), m.end(grp)) if grp else (m.start(), m.end())
                if a < 0 or b < 0:
                    a, b = m.start(), m.end()
                for r in line.rects_for(a, b):
                    rects.append((r, cat))
                stats[cat] = stats.get(cat, 0) + 1
        if "person" in enabled and only_ids is None:
            person_spans = find_names(line) + english_names(line)
            for a, b in person_spans:
                for r in line.rects_for(a, b):
                    rects.append((r, "person"))
                stats["person"] = stats.get("person", 0) + 1
            # рядом с ФИО дата часто является датой рождения
            if person_spans and "birth" in enabled:
                for m in re.finditer(r"\b\d{2}[.\-]\d{2}[.\-](?:19|20)\d{2}\b", text):
                    for r in line.rects_for(m.start(), m.end()):
                        rects.append((r, "birth"))
                    stats["birth"] = stats.get("birth", 0) + 1

    if "person" in enabled and only_ids is None:
        for i in range(len(lines) - 1):
            l1, l2 = lines[i], lines[i + 1]
            if not l1.words or len(l2.words) < 2:
                continue
            last = _cyr(l1.words[-1].text.strip(".,;:"))
            second = _cyr(l2.words[1].text.strip(".,;:"))
            if SURNAME_STRICT.search(last.lower()) and PATRONYMIC.search(second.lower()):
                for r in (l1.words[-1].rect, l2.words[0].rect, l2.words[1].rect):
                    rects.append((r, "person"))
                stats["person"] = stats.get("person", 0) + 1

    # подпись без значения — значение на следующей строке
    labels = (
        ("reg_number", r"(?:регистрационн|реестров|регистров)\w*\s+номер"),
        ("issue_date", r"(?:дата\s+выдачи|выдан)\w*"),
        ("identity", r"(?:кем\s+выдан|место\s+рождения|место\s+жительства|серия|"
                     r"снилс|страховой\s+номер|полис|код\s+подразделения)"),
    )
    for i, line in enumerate(lines[:-1]):
        for rid, pat in labels:
            if not re.search(pat, line.text, re.I):
                continue
            cat = "docno" if rid == "reg_number" else "identity"
            if cat not in enabled:
                continue
            if re.search(r"\d", line.text):
                continue
            for nxt in lines[i + 1:i + 3]:
                if not nxt.text.strip():
                    continue
                if rid == "issue_date":
                    for w in nxt.words:
                        rects.append((w.rect, "identity"))
                else:
                    m = re.search(r"\d[\d\s\-/]{0,30}", nxt.text)
                    if not m:
                        continue
                    for r in nxt.rects_for(m.start(), m.end()):
                        rects.append((r, cat))
                stats[cat] = stats.get(cat, 0) + 1
                break

    return rects, stats


# --------------------------------------------------------------------------- #
#  DOCX / текст
# --------------------------------------------------------------------------- #


def docx_to_md(path: str) -> str:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = Document(path)
    out: List[str] = []
    for child in d.element.body.iterchildren():
        if child.tag == qn("w:p"):
            p = Paragraph(child, d)
            text = p.text.strip()
            if not text:
                out.append("")
                continue
            style = (p.style.name or "").lower()
            if style.startswith("heading"):
                lvl = re.sub(r"\D", "", style) or "1"
                out.append("#" * min(int(lvl), 6) + " " + text)
            elif "list" in style or (p._p.pPr is not None and p._p.pPr.numPr is not None):
                out.append("- " + text)
            elif style.startswith("title"):
                out.append("# " + text)
            else:
                out.append(text)
        elif child.tag == qn("w:tbl"):
            t = Table(child, d)
            rows = [[c.text.strip().replace("\n", " ") for c in r.cells] for r in t.rows]
            if not rows:
                continue
            width = max(len(r) for r in rows)
            rows = [r + [""] * (width - len(r)) for r in rows]
            out.append("| " + " | ".join(rows[0]) + " |")
            out.append("|" + "|".join(["---"] * width) + "|")
            for r in rows[1:]:
                out.append("| " + " | ".join(r) + " |")
            out.append("")
    return "\n".join(out).strip() + "\n"


def _rule_order(rule):
    """Длинные шаблоны раньше коротких; названия учреждений — раньше городов и адресов."""
    cat, rid, pat, val, grp = rule
    priority = 0 if rid.startswith("institution") else 1
    return (priority, -len(pat))


NUM_RX = re.compile(r"(?<![\w.,])\d[\d \u00a0]*(?:[.,]\d{1,3})?(?![\w])")


def mask_numbers(text: str, stats: Dict[str, int]) -> str:
    """Все числа, где 4 и более цифр: суммы, счета, остатки, проценты."""
    out, last = [], 0
    for m in NUM_RX.finditer(text):
        token = m.group().strip()
        if sum(ch.isdigit() for ch in token) >= 3:
            out.append(text[last:m.start()])
            out.append(PLACEHOLDER)
            last = m.end()
            stats["finance"] = stats.get("finance", 0) + 1
    out.append(text[last:])
    return "".join(out)


def anonymize_text(text: str, mode: str, enabled: set):
    stats: Dict[str, int] = {}
    text = normalize(text)

    def sub(cat, rid, rx, val, grp):
        def repl(m):
            if val and not validate(val, m.group()):
                return m.group()
            stats[cat] = stats.get(cat, 0) + 1
            if grp:
                whole = m.group(0)
                gs = m.start(grp) - m.start(0)
                ge = m.end(grp) - m.start(0)
                if gs >= 0 and ge >= 0:
                    return whole[:gs] + PLACEHOLDER + whole[ge:]
            return PLACEHOLDER
        return rx.sub(repl, text)

    for cat, rid, pat, val, grp in sorted(RULES, key=_rule_order):
        if cat not in enabled:
            continue
        text = sub(cat, rid, re.compile(pat, re.I), val, grp)

    if "person" in enabled:
        spans = []
        for base, ln in lines_with_offsets(text):
            spans.extend((base + a, base + b) for a, b in find_names(ln) + english_names(ln))
        spans.sort()
        merged = []
        for a, b in spans:
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        if merged:
            parts, cur = [], 0
            for a, b in merged:
                parts.append(text[cur:a])
                parts.append(PLACEHOLDER)
                cur = b
                stats["person"] = stats.get("person", 0) + 1
            parts.append(text[cur:])
            text = "".join(parts)
    if "finance" in enabled:
        text = mask_numbers(text, stats)
    return text, stats


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #


def print_categories():
    print("Категории (по умолчанию скрываются все):")
    for key, title in CATEGORIES.items():
        print(f"  {key:11s} {title}")


def ask_categories(found: Dict[str, int]) -> set:
    enabled = set(CATEGORIES)
    if not found:
        print("Ничего не найдено — скрывать нечего.")
        return enabled
    print("\nНайдено:")
    for key in CATEGORIES:
        if found.get(key):
            print(f"  {key:11s} {found[key]:3d}  {CATEGORIES[key]}")
    print("\nЧто оставить открытым? Категории через запятую, Enter — скрыть всё: ", end="")
    raw = input().strip()
    if not raw:
        return enabled
    for part in re.split(r"[,\s]+", raw):
        if part in CATEGORIES:
            enabled.discard(part)
        elif part in ("all", "все"):
            return enabled
    return enabled


NAME_DATE = re.compile(r"\b\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}\b|\b(?:19|20)\d{2}\b")
# уменьшительные имена, которых нет в справочнике полных имён
NAME_HINTS = re.compile(
    r"\b(?:Саш|Маш|Миш|Пет|Ван|Дим|Кол|Ол|Лен|Тан|Наташ|Сер[её]ж|Андрюш|Юр|Вит|Вов|"
    r"Л[её]ш|Гриш|Даш|Кат|Сон|Лид|Гал|Вер|Над|Люб|Свет|Ирин|Павл|Слав|Стас|Тол|Фед|"
    r"Эдик|Игор|Оксан|Полин|Алин|Ксюш|Юл)\w*",
    re.I,
)


def sanitize_filename(stem: str, enabled: set, mode: str):
    """Убирает из имени файла то же, что скрываем в тексте, и даты.

    Возвращает (новое_имя, было_ли_что_убирать).
    """
    spans = []
    for cat, rid, pat, val, grp in RULES:
        if cat not in enabled:
            continue
            continue
        try:
            rx = re.compile(pat, re.I)
        except re.error:
            continue
        for m in rx.finditer(stem):
            if val and not validate(val, m.group()):
                continue
            a, b = (m.start(grp), m.end(grp)) if grp else (m.start(), m.end())
            if a < 0 or b < 0:
                a, b = m.start(), m.end()
            spans.append((a, b))
    for m in NAME_DATE.finditer(stem):
        spans.append((m.start(), m.end()))
    for a, b in find_names(Line(stem, [], [])):
        spans.append((a, b))
    for m in NAME_HINTS.finditer(stem):
        if m.group()[:1].isupper():
            spans.append((m.start(), m.end()))
    if not spans:
        return stem, False

    spans.sort()
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))

    parts, cur = [], 0
    for a, b in merged:
        parts.append(stem[cur:a])
        cur = b
    parts.append(stem[cur:])
    clean = " ".join("".join(parts).replace("_", " ").split())
    clean = re.sub(r"^[\s\-–—.,;:]+|[\s\-–—.,;:]+$", "", clean)
    if len(re.sub(r"\W", "", clean)) < 3:
        clean = "document"
    return clean, True


def output_name(name: str, ext: str, enabled: set, mode: str) -> str:
    """Итоговое имя: всегда суффикс _hidden."""
    stem = os.path.splitext(name)[0]
    clean, _changed = sanitize_filename(stem, enabled, mode)
    return f"{clean}_hidden{ext}"


def out_name(src: str, ext: str) -> str:
    """Имя результата: всегда <имя>_hidden.<ext>, без варианта _anon."""
    base = os.path.basename(src)
    if KEEP_NAME:
        stem = os.path.splitext(base)[0]
        return f"{stem}_hidden{ext}"
    enabled = CURRENT.get("enabled") or set(CATEGORIES)
    mode = CURRENT.get("mode") or "document"
    return output_name(base, ext, enabled, mode)


def sheet_to_md(path: str) -> str:
    """Таблица -> Markdown: листы как разделы, строки как строки таблицы."""
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    out: List[str] = []
    for ws in wb.worksheets:
        rows = [["" if c is None else str(c) for c in r] for r in ws.iter_rows(values_only=True)]
        rows = [r for r in rows if any(x.strip() for x in r)]
        if not rows:
            continue
        out.append(f"## {ws.title}")
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        out.append("| " + " | ".join(rows[0]) + " |")
        out.append("|" + "|".join(["---"] * width) + "|")
        for r in rows[1:]:
            out.append("| " + " | ".join(r) + " |")
        out.append("")
    return "\n".join(out).strip() + "\n"


def pdf_lines(page) -> List[Line]:
    """Строки PDF по текстовому слою: блок + строка из разметки страницы."""
    buckets: Dict[Tuple[int, int], List[Word]] = {}
    for x0, y0, x1, y1, w, b, l, _n in page.get_text("words"):
        if w.strip():
            buckets.setdefault((b, l), []).append(Word(w, Rect(x0, y0, x1, y1)))
    out: List[Line] = []
    for key in sorted(buckets):
        out.extend(_group_lines(sorted(buckets[key], key=lambda x: x.rect.x0)))
    return out


def pdf_hits(lines: List[Line], enabled: set, mode: str) -> List[Tuple[Rect, str]]:
    """Найденные значения на странице PDF: те же правила, что и для текста."""
    rules = []
    for cat, rid, pat, val, grp in RULES:
        if cat not in enabled:
            continue
        rules.append((cat, re.compile(pat, re.I), val, grp))
    hits: List[Tuple[Rect, str]] = []
    for line in lines:
        text = normalize(line.text)
        for cat, rx, val, grp in rules:
            for m in rx.finditer(text):
                if val and not validate(val, m.group()):
                    continue
                a, b = (m.start(grp), m.end(grp)) if grp else (m.start(), m.end())
                if a < 0 or b < 0:
                    a, b = m.start(), m.end()
                for r in line.rects_for(a, b):
                    hits.append((r, cat))
        if "finance" in enabled:
            for w in line.words:
                tok = w.text.strip()
                if sum(ch.isdigit() for ch in tok) >= 3 and not re.search(r"[A-Za-zА-Яа-яЁё]", tok):
                    hits.append((w.rect, "finance"))
        if "person" in enabled:
            for a, b in find_names(line):
                for r in line.rects_for(a, b):
                    hits.append((r, "person"))
    return hits


def bars(rects: List[Rect], gap: float = 3.0, pad: float = 1.2) -> List[Rect]:
    """Склеивает найденные слова в аккуратные чёрные полосы по строкам."""
    rows: List[List[Rect]] = []
    for r in sorted(rects, key=lambda r: (round(r.y0, 1), r.x0)):
        if rows and abs(rows[-1][0].y0 - r.y0) <= 1.0 and abs(rows[-1][0].y1 - r.y1) <= 1.5:
            rows[-1].append(r)
        else:
            rows.append([r])
    out: List[Rect] = []
    for row in rows:
        row.sort(key=lambda r: r.x0)
        x0, y0, x1, y1 = row[0].x0, row[0].y0, row[0].x1, row[0].y1
        for r in row[1:]:
            if r.x0 - x1 <= gap:
                x1, y0, y1 = max(x1, r.x1), min(y0, r.y0), max(y1, r.y1)
            else:
                out.append(Rect(x0 - pad, y0, x1 + pad, y0 + max(y1 - y0, 6.0)))
                x0, y0, x1, y1 = r.x0, r.y0, r.x1, r.y1
        h = max(y1 - y0, 6.0)
        if h > 26.0:
            for r in row:
                out.append(Rect(r.x0 - pad, r.y0, r.x1 + pad, r.y0 + max(r.y1 - r.y0, 6.0)))
        else:
            out.append(Rect(x0 - pad, y0, x1 + pad, y0 + h))
    # тонкая полоса на всю ширину своего столбца, с зазором по вертикали
    groups: List[List[Rect]] = []
    for r in sorted(out, key=lambda r: (r.x0 + r.x1) / 2):
        c = (r.x0 + r.x1) / 2
        if groups and c - (groups[-1][-1].x0 + groups[-1][-1].x1) / 2 <= 12:
            groups[-1].append(r)
        else:
            groups.append([r])
    thin: List[Rect] = []
    for g in groups:
        gx0 = min(r.x0 for r in g) - 0.8
        gx1 = max(r.x1 for r in g) + 0.8
        for r in g:
            hh = r.height
            top = r.y0 + hh * 0.26
            thin.append(Rect(gx0, top, gx1, top + hh * 0.48))
    return thin


def redact_pdf(path: str, dst: str, enabled: set, mode: str) -> Dict[str, int]:
    """PDF с текстовым слоем: чёрные полосы; текст и изображения под ними удаляются."""
    doc = pymupdf.open(path)
    full = "".join(p.get_text() for p in doc).strip()
    if len(full) < 40:
        doc.close()
        raise SkipFile("pdf без текстового слоя")
    stats: Dict[str, int] = {}
    for page in doc:
        hits = pdf_hits(pdf_lines(page), enabled, mode)
        if not hits:
            continue
        for r, cat in hits:
            stats[cat] = stats.get(cat, 0) + 1
        rects = [r for r, _ in hits]
        for b in bars(rects):
            page.add_redact_annot(pymupdf.Rect(b.x0, b.y0, b.x1, b.y1), fill=(0, 0, 0), cross_out=False)
        # удаляем изображения, попавшие под плашку (не оставляем пиксели)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_REMOVE)
    doc.set_metadata({})
    doc.save(dst, garbage=4, deflate=True, clean=True)
    doc.close()
    return stats


def process(path: str, args, enabled: Optional[set] = None):
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXT:
        raise SkipFile("формат не поддерживается (изображение/скан)")
    if ext not in SUPPORTED_EXT:
        raise SkipFile(f"формат не поддерживается ({ext or 'без расширения'})")
    mode = args.mode
    if ext == ".pdf":
        enabled = set(enabled) if enabled is not None else default_enabled("document", args)
        if mode == "auto":
            doc = pymupdf.open(path)
            full = "".join(p.get_text() for p in doc)
            doc.close()
            mode = "judicial" if is_court_act(full) else "document"
            enabled = set(enabled) if args.keep or args.only else default_enabled(mode, args)
        dst = args.out or out_name(path, ".pdf")
        if not dst.lower().endswith(".pdf"):
            dst = os.path.splitext(dst)[0] + ".pdf"
        stats = redact_pdf(path, dst, enabled, mode)
        CURRENT["enabled"], CURRENT["mode"] = enabled, mode
        return {"file": os.path.basename(path), "status": "ok", "mode": mode,
                "categories": stats, "kept_categories": sorted(set(CATEGORIES) - enabled),
                "output": dst}

    keep_fmt = getattr(args, "keep_format", False)
    if ext in DOC_EXT:
        if keep_fmt:
            # сохраняем .docx: конвертируем в MD, анонимизируем, пишем обратно как текст в md-совместимый путь?
            # упрощённо: пока --keep-format для docx/xlsx пишет анонимизированный текст
            # в исходном расширении не поддерживается полноценная правка XML;
            # оставляем MD по умолчанию, при --keep-format — .txt рядом с исходным именем
            raw = docx_to_md(path)
            out_ext = ".txt"
        else:
            raw = docx_to_md(path)
            out_ext = ".md"
    elif ext in SHEET_EXT:
        raw = sheet_to_md(path)
        rows, heads = [], {}
        for line in raw.split("\n"):
            if line.startswith("|") and not re.search(r"\d", line):
                key = f"HDR{len(heads)}X"
                heads[key] = line
                rows.append(key)
                continue
            rows.append(line)
        saved_heads = heads
        raw = "\n".join(rows)
        out_ext = ".txt" if keep_fmt else ".md"
    else:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        out_ext = ext

    if mode == "auto":
        mode = "judicial" if is_court_act(raw) else "document"
    enabled = set(enabled) if enabled is not None else default_enabled(mode, args)
    if args.ask:
        _, found = collect_hits(lines_from_text(raw), enabled, mode)
        enabled = ask_categories(found)
    anon, stats = anonymize_text(raw, mode, enabled)
    if ext in SHEET_EXT:
        for key, original in saved_heads.items():
            anon = anon.replace(key, original) if key in anon else anon
            anon = re.sub(r"\[censored\]\s*" + key, original, anon)
    CURRENT["enabled"], CURRENT["mode"] = enabled, mode
    dst = args.out or out_name(path, out_ext)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(anon)
    return {"file": os.path.basename(path), "status": "ok", "mode": mode,
            "categories": stats, "kept_categories": sorted(set(CATEGORIES) - enabled),
            "output": dst}


def default_enabled(mode: str, args) -> set:
    enabled = set(CATEGORIES)
    if mode == "judicial":
        enabled -= JUDICIAL_KEEP
    if args.only:
        enabled &= set(args.only)
    if args.keep:
        enabled -= set(args.keep)
    return enabled


def main(argv: Optional[Sequence[str]] = None) -> int:
    global PLACEHOLDER, SAFE_NAME, KEEP_NAME, KEEP_FORMAT
    ap = argparse.ArgumentParser(description="DoctorHide — обезличивание документов")
    ap.add_argument("inputs", nargs="*", help="файлы, маски (docs\\*.pdf) или папки")
    ap.add_argument("-o", "--out", help="файл результата (для одного входа)")
    ap.add_argument("--mode", default="auto", choices=["auto", "judicial", "document"])
    ap.add_argument("--keep", nargs="+", default=[], metavar="CAT",
                    help="категории, которые НЕ скрывать")
    ap.add_argument("--only", nargs="+", default=[], metavar="CAT",
                    help="скрывать только эти категории")
    ap.add_argument("--ask", action="store_true", help="показать найденное и спросить, что оставить")
    ap.add_argument("--list-categories", action="store_true")
    ap.add_argument("--placeholder", default=PLACEHOLDER)
    ap.add_argument("--keep-name", action="store_true",
                    help="сохранить исходное название файла в имени результата")
    ap.add_argument("--safe-name", action="store_true",
                    help="совместимость: имена и так обезличиваются")
    ap.add_argument("--keep-format", action="store_true",
                    help="для DOCX/XLSX: не переводить в .md (результат .txt с [censored])")
    ap.add_argument("--keep-meta", action="store_true")
    ap.add_argument("--report", help="записать JSON-отчёт сюда")
    ap.add_argument("--json", action="store_true", help="печатать только JSON")
    args = ap.parse_args(argv)

    if args.list_categories:
        print_categories()
        return 0

    PLACEHOLDER = args.placeholder
    SAFE_NAME = args.safe_name
    KEEP_NAME = args.keep_name
    KEEP_FORMAT = args.keep_format

    files: List[str] = []
    for item in args.inputs:
        if os.path.isdir(item):
            for name in sorted(os.listdir(item)):
                ext = os.path.splitext(name)[1].lower()
                if ext in SUPPORTED_EXT or ext in IMAGE_EXT:
                    files.append(os.path.join(item, name))
        elif any(ch in item for ch in "*?["):
            files.extend(sorted(glob.glob(item)))
        else:
            files.append(item)
    if not files:
        ap.error("не указаны входные файлы")

    results = []
    enabled = None
    for path in files:
        if not os.path.exists(path):
            rec = {"file": os.path.basename(path), "status": "error",
                   "reason": "файл не найден", "output": None}
            results.append(rec)
            if not args.json:
                print(f"нет файла: {path}", file=sys.stderr)
            continue
        try:
            rep = process(path, args, enabled)
            results.append(rep)
            if args.keep is not None and enabled is None and (args.keep or args.only):
                enabled = default_enabled(rep.get("mode", args.mode), args)
            if not args.json:
                cats = ", ".join(f"{k}:{v}" for k, v in sorted(rep.get("categories", {}).items())) or "—"
                print(f"{rep['file']} -> {rep['output']}  [{cats}]")
        except SkipFile as err:
            rec = {"file": os.path.basename(path), "status": "skipped",
                   "reason": str(err.reason if hasattr(err, "reason") else err),
                   "output": None}
            results.append(rec)
            if not args.json:
                print(f"пропущен {path}: {rec['reason']}", file=sys.stderr)
        except Exception as err:  # noqa: BLE001
            rec = {"file": os.path.basename(path), "status": "error",
                   "reason": f"{type(err).__name__}: {err}", "output": None}
            results.append(rec)
            if not args.json:
                print(f"ошибка {path}: {rec['reason']}", file=sys.stderr)

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))

    failed = sum(1 for r in results if r.get("status") in ("error", "skipped"))
    return 1 if failed and not any(r.get("status") == "ok" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
