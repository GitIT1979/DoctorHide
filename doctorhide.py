#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DoctorHide — обезличивание документов (PDF, DOCX, MD/TXT, изображения).

Что скрывается (по умолчанию — всё, что включено в профиле):
  person     ФИО (фамилия, имя, отчество)
  identity   документы личности: паспорт, водительское, загранпаспорт, военный билет,
             свидетельство о рождении, код подразделения
  codes      присвоенные коды: ИНН, КПП, ОГРН/ОГРНИП, ОКПО, ОКТМО, ОКАТО, ОКВЭД, СНИЛС, полис ОМС
  address    адреса проживания/регистрации
  realty     недвижимость и права на неё (собственность, аренда, кадастровый номер, площадь)
  contacts   телефоны, e-mail, мессенджеры
  bank       счета, карты, БИК, банк
  finance    доходы, зарплата, суммы, долги, кредиты, налоги
  property   движимое имущество: авто, VIN, госномер
  employment место работы и должность
  leisure    места отдыха: санатории, отели, курорты, туры
  birth      возраст и дата рождения
  kinship    родство
  org        наименования организаций
  docno      номера документов

Вывод:
  PDF        чёрные плашки (белые — по флагу --white-bars), исходный текст под ними
             удалён (apply_redactions); остальное содержимое остаётся текстом —
             файл читается и ищется.
             Скан-страница без текстового слоя пересобирается из того же растра
             с выжженными областями (вёрстка 1:1).
  Изображения PNG/JPEG/TIFF — плашки выжигаются в пикселях, формат сохраняется.
  QR-коды и штрихкоды в растре закрываются тем же сканером, что и для сканов PDF.
  DOCX       конвертируется в Markdown, значения заменяются на [censored].
  MD/TXT     значения заменяются на [censored].

Выбор пользователя:
  --list-categories        показать категории и выйти
  --keep cat1,cat2         не скрывать эти категории
  --only cat1,cat2         скрывать только эти категории
  --ask                    показать, что найдено, и спросить, что оставить

Примеры:
  python doctorhide.py doc.pdf
  python doctorhide.py doc.pdf --ask
  python doctorhide.py паспорт.png --keep docno
  python doctorhide.py решение.pdf --mode judicial
  python doctorhide.py "docs/*.pdf"
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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import pymupdf
from PIL import Image, ImageDraw

PLACEHOLDER = "[censored]"
KEEP_NAME = False
CURRENT = {"enabled": None, "mode": "document"}
BAR_BORDER = False
# Плашка — чёрная, как в шпионских фильмах. Белая заливка — по флагу --white-bars.
BAR_RGB = (0, 0, 0)
BAR_FILL = (0.0, 0.0, 0.0)
TEXT_LAYER = True
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

# --------------------------------------------------------------------------- #
#  Категории
# --------------------------------------------------------------------------- #

CATEGORIES: Dict[str, str] = {
    "person": "ФИО (фамилия, имя, отчество)",
    "identity": "Документы личности (паспорт, в/у, загранпаспорт, военный билет)",
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
    "org": "Наименования организаций",
    "docno": "Номера документов",
}

# в судебных актах эти категории по умолчанию НЕ скрываются
JUDICIAL_KEEP = {"person", "org", "docno"}

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
    ("contacts", "phone_local", r"(?<!\d)(?:\(\d{3}\)|\d{3})[\s\-]\d{3}[\s\-]\d{2}[\s\-]\d{2}(?!\d)", None, 0),
    ("contacts", "email", r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b", None, 0),
    ("contacts", "messenger",
     r"(?:telegram|whatsapp|whats\s?app|viber|телеграм|ватсап|вибер)[\s:]*@?[A-Za-z0-9_]{3,}", None, 0),
    # --- bank ---
    ("bank", "bank_acc", r"\b\d{20}\b", None, 0),
    ("bank", "bank_acc_spaced", r"(?<!\d)(?:\d{4}[ ]){4}\d{4}(?!\d)", None, 0),
    ("bank", "bik", r"(?:БИК\s*[:№]?\s*)?\b(04\d{7})\b", None, 1),
    ("bank", "card", r"\b(?:\d{4}[ -]?){3}\d{4}\b", None, 0),
    ("bank", "bank_name", r"\b[А-ЯЁ][А-Яа-яЁё\-]*(?:банк|Банк)\b", None, 0),
    ("bank", "account_terms",
     r"\b(?:расчётн\w+|расчетн\w+|р/с|к/с|корр\.?\s*сч\w*|корреспондентск\w+|лицев\w+\s+сч\w*|счёт|счет)\s*[:№]?\s*[\d\s]{5,25}", None, 0),
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
    # --- birth ---
    ("birth", "age", r"\b(?:возраст\w*|в\s+возрасте)\s*[:—-]?\s*\d{1,3}(?:\s*(?:лет|года|год|л\.))?", None, 0),
    ("birth", "age_years", r"\b\d{1,3}\s*(?:лет|года|год)\b", None, 0),
    ("birth", "dob", r"(?:дата\s+рождения|родил\w*|г\.\s*р\.)\s*[:—-]?\s*\d{1,2}[./]\d{1,2}[./]\d{2,4}", None, 0),
    ("birth", "dob_plain", r"\b\d{1,2}[./]\d{1,2}[./](?:19|20)\d{2}\s*(?:г\.?\s*)?(?:р\.|года рождения)", None, 0),
    # сокрытию подлежат все даты, а не только рождения и выдачи документов
    ("birth", "date_num", r"\b\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}\b", None, 0),
    ("birth", "date_words_short", r"\b\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+(?:19|20)\d{2}\b", None, 0),
    ("birth", "year_word", r"\b(?:19|20)\d{2}\s*(?:г\.|год|года|году)\b", None, 0),
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
    ("docno", "long_number", r"\b\d{15,}\b", None, 0),
]

BLOCK_RULES = {
    "passport", "passport_code", "drv_series", "snils", "phone", "phone_local", "bank_acc",
    "bank_acc_spaced", "bik", "inn", "kpp", "ogrn", "long_number", "gov_body",
    "legal_entity", "addr_street", "street_before", "addr_house", "city_label", "region",
    "city", "cadastral", "realty_obj", "account_terms", "card", "email", "messenger",
}

ID_RULES = {"inn", "kpp", "ogrn", "snils", "passport", "passport_code", "drv_series",
            "phone", "email", "bank_acc", "bik", "card", "long_number"}

# участники суда: их ФИО в судебных актах сохраняются
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
    rect: pymupdf.Rect


@dataclass
class Line:
    text: str
    words: List[Word]
    starts: List[int]

    def rects_for(self, a: int, b: int) -> List[pymupdf.Rect]:
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


def page_lines(page: pymupdf.Page) -> List[Line]:
    buckets: Dict[Tuple[int, int], List[Word]] = {}
    for x0, y0, x1, y1, w, b, l, _n in page.get_text("words"):
        if w.strip():
            buckets.setdefault((b, l), []).append(Word(w, pymupdf.Rect(x0, y0, x1, y1)))
    out = []
    for key in sorted(buckets):
        ws = sorted(buckets[key], key=lambda x: x.rect.x0)
        out.extend(_group_lines(ws))
    return out


def page_blocks(page: pymupdf.Page) -> List[Line]:
    buckets: Dict[int, List[Tuple[int, float, Word]]] = {}
    for x0, y0, x1, y1, w, b, l, _n in page.get_text("words"):
        if w.strip():
            buckets.setdefault(b, []).append((l, x0, Word(w, pymupdf.Rect(x0, y0, x1, y1))))
    out = []
    for b in sorted(buckets):
        ws = [t[2] for t in sorted(buckets[b], key=lambda t: (t[0], t[1]))]
        out.extend(_group_lines(ws))
    return out


def _is_upperish(s: str) -> bool:
    letters = [c for c in s if c.isalpha()]
    if len(letters) < 3:
        return False
    return sum(1 for c in letters if c.isupper()) / len(letters) >= 0.7


def merge_upper_runs(lines: List[Line]) -> List[Line]:
    if not lines:
        return []
    lines = sorted(lines, key=lambda ln: (round(ln.words[0].rect.y0, 1), ln.words[0].rect.x0))
    runs, cur = [], []
    for ln in lines:
        upper = _is_upperish(ln.text)
        if not cur:
            if upper:
                cur = [ln]
            continue
        prev = cur[-1]
        dy = ln.words[0].rect.y0 - prev.words[0].rect.y1
        dx = abs(ln.words[0].rect.x0 - prev.words[0].rect.x0)
        if upper and -2 <= dy <= 25 and dx <= 25:
            cur.append(ln)
        else:
            if len(cur) > 1:
                runs.extend(_group_lines([w for l in cur for w in l.words]))
            cur = [ln] if upper else []
    if len(cur) > 1:
        runs.extend(_group_lines([w for l in cur for w in l.words]))
    return runs


def passport_pairs(words: List[Word]) -> List[pymupdf.Rect]:
    fours = [w for w in words if re.fullmatch(r"\d{4}", w.text)]
    sixes = [w for w in words if re.fullmatch(r"\d{6}", w.text)]
    out = []
    for f in fours:
        best = None
        for s in sixes:
            dx = s.rect.x0 - f.rect.x1
            dy = s.rect.y0 - f.rect.y0
            if -10 <= dx <= 300 and -40 <= dy <= 80:
                if best is None or abs(dx) + abs(dy) < best[0]:
                    best = (abs(dx) + abs(dy), s)
        if best:
            out.append(f.rect | best[1].rect)
    return out


def lines_from_text(text: str) -> List[Line]:
    out = []
    for raw in text.split("\n"):
        if not raw.strip():
            continue
        words, starts = [], []
        for m in re.finditer(r"\S+", raw):
            words.append(Word(m.group(), pymupdf.Rect(0, 0, 0, 0)))
            starts.append(m.start())
        out.append(Line(raw, words, starts))
    return out


def lines_with_offsets(text: str) -> List[Tuple[int, Line]]:
    out, pos = [], 0
    for raw in text.split("\n"):
        if raw.strip():
            words, starts = [], []
            for m in re.finditer(r"\S+", raw):
                words.append(Word(m.group(), pymupdf.Rect(0, 0, 0, 0)))
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


def _given_like(low: str) -> bool:
    """Имя из справочника или двойное имя: любая часть через дефис — имя
    («софья-анна», «джан-пьер»). Фамилии двойные ловятся отдельно."""
    if low in GIVEN_NAMES:
        return True
    if "-" in low:
        parts = [p for p in low.split("-") if p]
        return (len(parts) >= 2 and any(p in GIVEN_NAMES for p in parts)
                and all(re.fullmatch(r"[а-яё]+", p) for p in parts))
    return False


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
    return _given_like(low) or bool(SURNAME.search(low)) or bool(PATRONYMIC.search(low))


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
        if _given_like(low) and i + 1 < n and as_name(toks[i + 1][2]):
            spans.append((a, toks[i + 1][1]))
            continue
        if i > 0 and toks[i - 1][2].lower() in NAME_MARKERS:
            nxt = low
            if not (_given_like(nxt) or (name_tok(t) and SURNAME_STRICT.search(nxt))):
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


def _norm_tok(tok: str) -> str:
    return _cyr(tok).strip(".,;:()«»\"'").lower()


def cross_line_name_spans(lines: List[Line]) -> List[Tuple[int, int, int]]:
    """ФИО, разорванное по строкам: «Иванов» / «Иван Иванович» — с разрывом или без.

    Возвращает (индекс строки, начало, конец) в её нормализованном тексте.
    Срабатывает на стыке соседних непустых строк, если хвост первой похож на
    фамилию (или имя), а начало второй — на имя, фамилию или отчество. Строки
    должны быть визуально близки: зазор не больше пары высот строки — пустая
    строка между частями ФИО допустима, соседний абзац — нет.
    """
    spans: List[Tuple[int, int, int]] = []
    idx = [i for i, ln in enumerate(lines) if ln.text.strip()]
    for k in range(len(idx) - 1):
        i, j = idx[k], idx[k + 1]
        l1, l2 = lines[i], lines[j]
        t1, t2 = _tokens(l1), _tokens(l2)
        if not t1 or not t2 or len(l2.words) > 4:
            continue
        if l1.words and l2.words:
            h = max(l1.words[-1].rect.height, l2.words[0].rect.height, 1.0)
            dy = l2.words[0].rect.y0 - l1.words[-1].rect.y1
            if dy > max(30.0, 2.4 * h):
                continue
        a, b = _norm_tok(t1[-1][2]), _norm_tok(t2[0][2])
        if len(a) < 2 or len(b) < 2 or a in NAME_STOP or b in NAME_STOP:
            continue
        a_surname = bool(SURNAME_STRICT.search(a))
        b_surname = bool(SURNAME_STRICT.search(b))
        pair = ((a_surname and (_given_like(b) or bool(PATRONYMIC.search(b))))
                or (_given_like(a) and (b_surname or bool(PATRONYMIC.search(b)))))
        if not pair:
            continue
        spans.append((i, t1[-1][0], t1[-1][1]))
        rest_name_like = len(t2) <= 3 and all(
            _given_like(_norm_tok(t[2])) or bool(SURNAME.search(_norm_tok(t[2])))
            or bool(PATRONYMIC.search(_norm_tok(t[2]))) for t in t2)
        spans.append((j, t2[0][0], t2[-1][1] if rest_name_like else t2[0][1]))
    return spans


def cross_line_contact_spans(lines: List[Line]) -> List[Tuple[int, int, int]]:
    """E-mail и телефон, разорванные переносом строки: «ivanov@» / «example.ru».

    Возвращает (индекс строки, начало, конец) в её нормализованном тексте.
    """
    spans: List[Tuple[int, int, int]] = []
    idx = [i for i, ln in enumerate(lines) if ln.text.strip()]
    for k in range(len(idx) - 1):
        i, j = idx[k], idx[k + 1]
        t1, t2 = normalize(lines[i].text), normalize(lines[j].text)
        m1 = re.search(r"[\w.+-]+@$", t1)
        m2 = re.match(r"([A-Za-zА-Яа-яЁё0-9-]+(?:\.[A-Za-zА-Яа-яЁё0-9-]+)+)", t2)
        if m1 and m2:
            spans.append((i, m1.start(), m1.end()))
            spans.append((j, m2.start(1), m2.end(1)))
            continue
        p1 = re.search(r"(\+7|8)[\s\-]?\(?\d{3}[\s\-)]?$", t1)
        m3 = re.match(r"\d{3}[\s\-]?\d{2}[\s\-]?\d{2}\b", t2)
        if p1 and m3:
            spans.append((i, p1.start(), len(t1)))
            spans.append((j, 0, m3.end()))
            continue
        p2 = re.search(r"\d{3}[\s\-]\d{2}$", t1)
        if p2 and re.match(r"\d{2}(?!\d)", t2):
            m4 = re.match(r"\d{2}", t2)
            spans.append((i, p2.start(), p2.end()))
            spans.append((j, 0, m4.end()))
    return spans


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


def label_zones(lines: List[Line], right_edge: float, page_right: float):
    """Значения рядом с подписями: справа по строке и строкой ниже.

    В таблицах («Дата рождения» сверху, «20.09.1977» под ней) шаблон «подпись + значение
    в одной строке» не срабатывает, и значение остаётся открытым.
    """
    out: List[Tuple[pymupdf.Rect, str]] = []
    ranked = sorted([ln for ln in lines if ln.words], key=lambda ln: (ln.words[0].rect.y0,
                                                                    ln.words[0].rect.x0))
    bands = [(min(w.rect.y0 for w in ln.words), max(w.rect.y1 for w in ln.words),
              ln.words[0].rect.x0) for ln in ranked]

    def row_end(y0: float, y1: float, x_end: float) -> float:
        """Ближайший соседний блок справа в той же строке — до него и тянем плашку."""
        best = right_edge + 8
        for a, b, x in bands:
            if b > y0 + 1 and a < y1 - 1 and x > x_end + 2:
                best = min(best, x - 4)
        return min(best, page_right - 8)

    for idx, line in enumerate(ranked):
        text = normalize(line.text)
        found = list(FIELD_LABEL_RX.finditer(text))
        for m in found:
            if len(text[:m.start()].split()) > 2:
                continue  # подпись должна открывать строку, а не тонуть в тексте
            cat = label_category(m.group())
            band = line.rects_for(m.start(), m.end())
            if not band:
                continue
            y0 = min(r.y0 for r in band) - 1.5
            y1 = max(r.y1 for r in band) + 1.5
            x_end = max(r.x1 for r in band)
            nexts = [nb for nb in found if nb.start() > m.end()]
            if nexts:
                nb = line.rects_for(nexts[0].start(), nexts[0].end())
                if nb:
                    x_end_gap = min(r.x0 for r in nb) - 4
                else:
                    x_end_gap = page_right
            else:
                x_end_gap = page_right
            tail = text[m.end():].strip(" :—-.,№")
            lab_end = row_end(y0, y1, x_end)
            # плашка тянется только до конца значения, а не до края листа
            line_end = max(w.rect.x1 for w in line.words)
            if tail:
                x1 = min(line_end + 6, lab_end, x_end_gap)
                if x1 - x_end >= 10:
                    # +3 — запас на поля плашки, иначе срезается последняя буква подписи
                    out.append((pymupdf.Rect(x_end + 3.0, y0, x1, y1), cat))
                continue
            placed = False
            for nline in ranked[idx + 1: idx + 3]:
                ny0 = min(w.rect.y0 for w in nline.words)
                ny1 = max(w.rect.y1 for w in nline.words)
                if ny0 - y1 > 30:
                    break
                ntext = normalize(nline.text).strip()
                if not ntext or (FIELD_LABEL_RX.match(ntext) and len(ntext) < 30):
                    continue
                nline_end = max(w.rect.x1 for w in nline.words)
                x1 = min(nline_end + 30, row_end(ny0, ny1, min(w.rect.x0 for w in nline.words) - 1),
                         lab_end, x_end_gap)
                if x1 - min(w.rect.x0 for w in nline.words) < 8:
                    continue
                # верх плашки держим у самой строки значения: иначе она срезает
                # нижние буквы подписи на строке выше
                out.append((pymupdf.Rect(min(w.rect.x0 for w in nline.words) - 1.5, ny0 - 0.8,
                                         x1, ny1 + 0.8), cat))
                placed = True
                break
            if placed:
                continue
            # значения OCR не прочитал, но у подписи есть своё поле — закрываем полосу
            h = max(8.0, y1 - y0)
            band_x1 = min(page_right - 8, x_end + 240)
            if band_x1 - x_end > 24:
                out.append((pymupdf.Rect(x_end + 3.0, y1 + 0.5, band_x1, y1 + 1.9 * h), cat))
    return out


# --------------------------------------------------------------------------- #
#  Документы личности
# --------------------------------------------------------------------------- #

IDENT_DOC_RX = re.compile(
    r"паспорт\s+гражданина\s+российской\s+федерации|"
    r"свидетельств\w*\s+о\s+(?:рождении|заключении\s+брака|расторжении\s+брака|удочерении|"
    r"усыновлении|смерти|перемене\s+имени)|"
    r"страхово\w*\s+свидетельств\w*|"
    r"полис\w*\s+(?:обязательного\s+медицинского\s+страхования|омс)|"
    r"справк\w*\s+о\s+регистрации|"
    r"военн\w+\s+билет|водительск\w+\s+удостоверение",
    re.I,
)


# Поля бланков личности: по ним видно, что на странице паспорт/свидетельство/полис.
# Между словами двусловных подписей допускаем мусор OCR («Дата = рождения»).
IDENT_FIELDS_RX = re.compile(
    r"(?<![А-Яа-яЁё])(?:фамили[яюи]|отчество|кем\s*[^А-Яа-яЁё\n]{0,4}\s*выдан\w*|код\s+подразделения|"
    r"место\s*[^А-Яа-яЁё\n]{0,4}\s*рождения|дата\s*[^А-Яа-яЁё\n]{0,4}\s*рождения|"
    r"дата\s*[^А-Яа-яЁё\n]{0,4}\s*выдачи|серия(?:\s+документа)?|"
    r"страховой\s+номер|снилс|полис\w*|удостоверение\w*|паспорт\w*)(?![А-Яа-яЁё])",
    re.I,
)


def page_has_identity_fields(lines: List[Line], need: int = 2) -> bool:
    """Страница похожа на бланк личности: есть подписи его полей.

    Для коротких страниц хватает одной подписи: на свидетельствах и полисах
    OCR вторую подпись часто читает с мусором («Дата = рождения»), и две
    чистые подписи не набираются — тогда фамилия одним словом оставалась
    незакрытой."""
    hits = 0
    n = 0
    for line in lines:
        if not line.text.strip():
            continue
        n += 1
        if IDENT_FIELDS_RX.search(normalize(line.text)):
            hits += 1
        if hits >= need:
            return True
    return hits >= 1 and n <= 12


# слова подписей полей: их оставляем читаемыми
LABEL_WORDS = {
    "фамилия", "имя", "отчество", "дата", "рождения", "место", "выдачи", "кем", "выдан",
    "серия", "номер", "код", "подразделения", "пол", "гражданство", "национальность",
    "адрес", "жительства", "регистрации", "пребывания", "подпись", "м", "п", "страховой",
    "снилс", "полис", "орган", "статус", "установлен", "документ", "удостоверение",
    "свидетельство", "о", "об", "и", "записи", "акта", "составления", "государственной",
    "приобретения", "расторжения", "брака", "родства", "опекуна", "родителей", "отец", "мать",
    "выдавший", "когда", "где", "каким", "органом", "код", "предъявителя",
}
# машинная зона паспорта: строки латиницей с «<<» — фамилия, номер, даты
MRZ_RX = re.compile(r"[A-Z0-9<]{15,}")


def _is_mrz(text: str) -> bool:
    compact = text.replace(" ", "")
    return any(m.group().count("<") >= 3 for m in MRZ_RX.finditer(compact))


def photo_blob_rects(page: pymupdf.Page, dpi: int = 100) -> List[pymupdf.Rect]:
    """Фотография на скане. Текстом она не является, отдельной картинкой — тоже."""
    try:
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    except Exception:
        return []
    im = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    return blob_rects_in_image(im, page.rect)


def blob_rects_in_image(im, page_rect: pymupdf.Rect) -> List[pymupdf.Rect]:
    """Компактные пятна полутонов: фотография в бланке, а не текст и не бумага.

    У текста и у бумаги средний тон ячейки почти белый, у фото — серый.
    """
    W, H = im.size
    if W < 60 or H < 60:
        return []
    cell = 12
    gw, gh = W // cell, H // cell
    if gw < 4 or gh < 4:
        return []
    small = im.convert("L").resize((gw, gh), Image.BOX)
    px = small.load()
    hot = [[70 <= px[x, y] <= 208 for x in range(gw)] for y in range(gh)]
    sc = page_rect.width / float(W)
    page_area = page_rect.width * page_rect.height

    seen = [[False] * gw for _ in range(gh)]
    blobs: List[Tuple[int, pymupdf.Rect]] = []
    for gy in range(gh):
        for gx in range(gw):
            if not hot[gy][gx] or seen[gy][gx]:
                continue
            stack, cells = [(gx, gy)], []
            seen[gy][gx] = True
            while stack:
                cx, cy = stack.pop()
                cells.append((cx, cy))
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < gw and 0 <= ny < gh and hot[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            xs = [c[0] for c in cells]
            ys = [c[1] for c in cells]
            box = pymupdf.Rect(min(xs) * cell * sc, min(ys) * cell * sc,
                               (max(xs) + 1) * cell * sc, (max(ys) + 1) * cell * sc)
            area = box.width * box.height
            if not (page_area * 0.03 <= area <= page_area * 0.20):
                continue
            ratio = box.width / max(box.height, 1)
            if not (0.5 <= ratio <= 1.1):
                continue
            fill = len(cells) * (cell * sc) ** 2 / max(area, 1)
            if fill < 0.65:
                continue
            blobs.append((len(cells), box))
    blobs.sort(key=lambda t: -t[0])
    return [b for _n, b in blobs[:2]]


# --------------------------------------------------------------------------- #
#  Детект
# --------------------------------------------------------------------------- #


def is_court_act(text: str) -> bool:
    hits = [p for p in COURT_MARKERS if re.search(p, text, re.I)]
    strong = bool(re.search(r"дело\s*№|судебн\w+\s+(?:акт|заседани)|СУД\b|истец|ответчик", text, re.I))
    return len(hits) >= 4 and strong


def collect_hits(lines: List[Line], enabled: set, mode: str, only_ids: Optional[set] = None,
                 keep_ids: bool = False, page_rect: Optional[pymupdf.Rect] = None):
    rects: List[Tuple[pymupdf.Rect, str]] = []
    stats: Dict[str, int] = {}
    # строка-заголовок бланка («СВИДЕТЕЛЬСТВО О РОЖДЕНИИ») остаётся читаемой:
    # персональных данных в ней нет, а по ней видно, что это за документ
    keep_lines = set()
    for line in lines[:8]:
        text = normalize(line.text).strip()
        if len(text) > 80:
            continue
        m = IDENT_DOC_RX.search(text)
        if m and (m.end() - m.start()) >= max(10, len(text) * 0.5):
            keep_lines.add(id(line))
    compiled = []
    for cat, rid, pat, val, grp in RULES:
        if cat not in enabled:
            continue
        if only_ids is not None and rid not in only_ids:
            continue
        if mode == "judicial" and keep_ids and rid in ID_RULES:
            continue
        compiled.append((cat, rid, re.compile(pat, re.I), val, grp))

    for line in lines:
        if not line.text.strip() or id(line) in keep_lines:
            continue
        text = normalize(line.text)
        for cat, rid, rx, val, grp in compiled:
            for m in rx.finditer(text):
                if val and not validate(val, m.group()):
                    continue
                if mode == "judicial" and re.search(r"(?<![а-яё])суд", m.group(), re.I):
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
            # в строке с ФИО даты — это даты рождения, их тоже закрываем
            if person_spans and "birth" in enabled and not keep_ids:
                for m in re.finditer(r"\b\d{2}[.\-]\d{2}[.\-](?:19|20)\d{2}\b", text):
                    for r in line.rects_for(m.start(), m.end()):
                        rects.append((r, "birth"))
                    stats["birth"] = stats.get("birth", 0) + 1
    if "person" in enabled and only_ids is None and not keep_ids:
        for li, a, b in cross_line_name_spans(lines):
            for r in lines[li].rects_for(a, b):
                rects.append((r, "person"))
            stats["person"] = stats.get("person", 0) + 1

    # e-mail и телефон, разорванные переносом строки: «ivanov@» / «example.ru»
    if "contacts" in enabled:
        for li, a, b in cross_line_contact_spans(lines):
            for r in lines[li].rects_for(a, b):
                rects.append((r, "contacts"))
            stats["contacts"] = stats.get("contacts", 0) + 1

    # подпись без значения, а само значение — на следующей строке (частый случай в сканах)
    if not keep_ids:
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
                if any(cat not in enabled for cat in (("docno",) if rid == "reg_number" else ("identity",))):
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
                            rects.append((r, "docno"))
                    stats["docno" if rid == "reg_number" else "identity"] =                         stats.get("docno" if rid == "reg_number" else "identity", 0) + 1
                    break

    # правая граница текста на странице — до неё тянутся плашки значений
    right_edge = max((w.rect.x1 for ln in lines for w in ln.words), default=0.0)
    page_right = page_rect.width if page_rect is not None else right_edge + 60

    # рукописная дата справа от подписи: закрываем всю правую часть строки
    if "identity" in enabled and not keep_ids:
        for line in lines:
            m = re.search(r"дата\s+выдачи", normalize(line.text), re.I)
            if not m or not line.words:
                continue
            label_rects = line.rects_for(m.start(), m.end())
            if not label_rects:
                continue
            y0 = min(w.rect.y0 for w in line.words) - 5
            y1 = max(w.rect.y1 for w in line.words) + 5
            # дата обычно дописана от руки: OCR читает её обрывками, поэтому
            # закрываем всё от подписи до правого края текста
            x0 = max(r.x1 for r in label_rects) + 2
            x1 = min(page_right - 8, right_edge + 8)
            if x1 - x0 < 8:
                continue
            rects.append((pymupdf.Rect(x0, y0, x1, y1), "identity"))
            stats["identity"] = stats.get("identity", 0) + 1

    # заверительная надпись «ВЕРНО» внизу листа: под ней подпись, дата и фамилия
    if page_rect is not None and "identity" in enabled and not keep_ids:
        for line in lines:
            if not line.words:
                continue
            if not re.fullmatch(r"(?:КОПИЯ\s+)?ВЕРН\w*[:.]?", normalize(line.text).strip().upper()):
                continue
            y0 = max(w.rect.y1 for w in line.words) + 1
            if y0 < page_rect.height * 0.55:
                continue
            x0 = min(w.rect.x0 for w in line.words) - 3
            rects.append((pymupdf.Rect(x0, y0, min(page_right - 8, x0 + 190),
                                       min(page_rect.height - 12, y0 + 70)), "identity"))
            stats["identity"] = stats.get("identity", 0) + 1

    # на бланке личности значение — это одиночное слово: «Кузнецова», «Петров», «Тверь».
    # Здесь закрываем любую фамилию/имя без соседей, иначе они остаются открытыми
    if "person" in enabled and not keep_ids and page_has_identity_fields(lines):
        for line in lines:
            if id(line) in keep_lines:
                continue
            text = normalize(line.text)
            for m in re.finditer(r"(?<![А-Яа-яЁё])[А-ЯЁ][А-Яа-яЁё\-]{2,}(?![А-Яа-яЁё])", text):
                low = m.group().lower()
                if low in NAME_STOP or low in CAPS_HYPHEN_STOP:
                    continue
                if not (_given_like(low) or SURNAME_STRICT.search(low) or PATRONYMIC.search(low)):
                    continue
                for r in line.rects_for(m.start(), m.end()):
                    rects.append((r, "person"))
                stats["person"] = stats.get("person", 0) + 1

    # страница с подписью «дата рождения» (даже если OCR разорвал её на две строки):
    # все даты на такой странице — личные даты
    page_text = " ".join(normalize(ln.text) for ln in lines).lower()
    mentions_dob = (("рождени" in page_text and "дата" in page_text) or "дата рождения" in page_text
                    or any(k in page_text for k in ("полис", "снилс", "омс", "свидетельство")))
    if ({"birth", "identity"} & enabled) and not keep_ids and mentions_dob:
        for line in lines:
            if id(line) in keep_lines:
                continue
            for m in re.finditer(r"\b\d{1,2}[.\-/]\d{2}[.\-/]\d{2,4}\b", normalize(line.text)):
                for r in line.rects_for(m.start(), m.end()):
                    rects.append((r, "birth"))
                stats["birth"] = stats.get("birth", 0) + 1

    # машинная зона паспорта: в ней и фамилия, и дата рождения, и номер — закрываем строку целиком
    if "identity" in enabled and not keep_ids:
        for line in lines:
            if id(line) in keep_lines:
                continue
            if _is_mrz(normalize(line.text)):
                for w in line.words:
                    rects.append((w.rect, "identity"))
                stats["identity"] = stats.get("identity", 0) + 1

    # имя или отчество отдельным словом в любой строке: «АЛЕКСАНДР», «ВЯЧЕСЛАВ», «АЛЕКСАНДРОВИЧ»
    if "person" in enabled and not keep_ids:
        for line in lines:
            if id(line) in keep_lines:
                continue
            for w in line.words:
                word = w.text.strip(".,;:()")
                low = _cyr(word).lower()
                if not re.fullmatch(r"[А-ЯЁ][А-Яа-яЁё\-]{2,}", _cyr(word)):
                    continue
                if _given_like(low) or PATRONYMIC.search(low):
                    rects.append((w.rect, "person"))
                    stats["person"] = stats.get("person", 0) + 1

    # значения рядом с подписями: справа по строке и в ячейке ниже
    for zone, cat in label_zones(lines, right_edge, page_right):
        if cat not in enabled:
            continue
        rects.append((zone, cat))
        stats[cat] = stats.get(cat, 0) + 1

    return rects, stats


def merge_rects(rects: Sequence[pymupdf.Rect], pad_x: float = 1.4, pad_y: float = 1.0,
                row_gap: float = 14.0, row_tol: float = 3.5) -> List[pymupdf.Rect]:
    """Собирает плашки в сплошные белые полосы.

    Соседние слова одной строки склеиваются, если зазор меньше row_gap, поэтому
    «ЯКОВЛЕВ-ЗАВАЛЬЕВСКИЙ ВЯЧЕСЛАВ АЛЕКСАНДРОВИЧ» закрывается одной полосой,
    а не тремя обрубками. Поля по краям увеличены — текст не выглядывает из-под плашки.
    """
    boxes = [pymupdf.Rect(r.x0 - pad_x, r.y0 - pad_y, r.x1 + pad_x, r.y1 + pad_y) for r in rects]
    merged = True
    while merged:
        merged = False
        out: List[pymupdf.Rect] = []
        for r in boxes:
            for i, o in enumerate(out):
                same_row = abs(o.y0 - r.y0) <= row_tol and abs(o.y1 - r.y1) <= row_tol
                near = (r.x0 - o.x1) <= row_gap and (o.x0 - r.x1) <= row_gap
                if same_row and near:
                    out[i] = o | r
                    merged = True
                    break
            else:
                out.append(r)
        boxes = out
    return boxes


# --------------------------------------------------------------------------- #
#  OCR
# --------------------------------------------------------------------------- #


def find_tesseract() -> Optional[str]:
    cmd = os.environ.get("TESSERACT_CMD")
    if cmd and os.path.exists(cmd):
        return cmd
    for p in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
              r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
              os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe")):
        if os.path.exists(p):
            return p
    return shutil.which("tesseract")


def tesseract_env() -> dict:
    env = os.environ.copy()
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tools", "tessdata")
    if os.path.isdir(local):
        env["TESSDATA_PREFIX"] = local
    return env


def _ocr_tsv(img_path: str, exe: str, lang: str = "rus+eng", psm: str = "3") -> List[str]:
    res = subprocess.run([exe, img_path, "stdout", "-l", lang, "--psm", psm, "tsv"],
                         capture_output=True, check=True, env=tesseract_env())
    return res.stdout.decode("utf-8", "replace").splitlines()


def _lines_from_tsv(rows: List[str], scale: float) -> List[Line]:
    words: List[Word] = []
    for row in rows[1:]:
        f = row.split("\t")
        if len(f) < 12 or not f[11].strip():
            continue
        left, top, w, h = int(f[6]), int(f[7]), int(f[8]), int(f[9])
        words.append(Word(f[11].strip(),
                          pymupdf.Rect(left * scale, top * scale, (left + w) * scale, (top + h) * scale)))
    return rows_from_words(words)


def rows_from_words(words: List[Word]) -> List[Line]:
    """Собирает слова в строки по геометрии, а не по разбиению OCR.

    Tesseract рвёт одну строку на две, если части попали в разные блоки
    («ЯКОВЛЕВА-ЗАВАЛЬ» и «ЕВСКАЯ»): по геометрии строка собирается целиком.
    """
    if not words:
        return []
    ws = sorted(words, key=lambda w: ((w.rect.y0 + w.rect.y1) / 2.0, w.rect.x0))
    rows: List[List[Word]] = []
    cur: List[Word] = []
    cy = 0.0
    for w in ws:
        c = (w.rect.y0 + w.rect.y1) / 2.0
        h = max(w.rect.height, 1.0)
        if cur and abs(c - cy) <= h * 0.45:
            cur.append(w)
            cy = (cy * (len(cur) - 1) + c) / len(cur)
        else:
            if cur:
                rows.append(cur)
            cur = [w]
            cy = c
    if cur:
        rows.append(cur)
    out: List[Line] = []
    for r in rows:
        out.extend(_group_lines(sorted(r, key=lambda w: w.rect.x0)))
    return out


def _ocr_quality(lines: List[Line]) -> int:
    """Насколько правдоподобен распознанный текст: словари и правдоподобные слова."""
    good = 0
    for ln in lines:
        for tok in re.findall(r"[А-Яа-яЁё\-]{3,}", ln.text):
            low = tok.lower()
            if low in GIVEN_NAMES or low in NAME_STOP or low in LABEL_WORDS:
                good += 2
            elif re.search(r"[аеиоуыэюя]", low) and len(low) >= 4:
                good += 1
    return good


def ocr_page(page: pymupdf.Page, exe: str, dpi: int) -> List[Line]:
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "p.png")
        page.get_pixmap(dpi=dpi, alpha=False).save(p)
        best = _lines_from_tsv(_ocr_tsv(p, exe), 72.0 / dpi)
        quality = _ocr_quality(best)
        # второй проход «единым блоком»: на фото бланков он часто читает лучше.
        # На странице, которая и так прочиталась хорошо, весь смысл в нём —
        # экономия: полная страница OCR — самая дорогая операция пайплайна.
        if quality < 400:
            alt = _lines_from_tsv(_ocr_tsv(p, exe, psm="6"), 72.0 / dpi)
            if _ocr_quality(alt) > quality * 1.2:
                best = alt
                quality = _ocr_quality(best)
    words = [w for ln in best for w in ln.words]
    # мелкий шрифт в углах страницы Tesseract часто теряет при разборе всей страницы;
    # непрочитанные полосы дочитываем отдельными фрагментами
    for band in unread_bands(page, words):
        try:
            pix = page.get_pixmap(dpi=int(dpi * 1.5), clip=band, alpha=False)
        except Exception:
            continue
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "band.png")
            pix.save(p)
            rows = _ocr_tsv(p, exe, psm="6")
        for ln in _lines_from_tsv(rows, 72.0 / (dpi * 1.5)):
            for w in ln.words:
                w.rect = pymupdf.Rect(w.rect.x0 + band.x0, w.rect.y0 + band.y0,
                                      w.rect.x1 + band.x0, w.rect.y1 + band.y0)
                words.append(w)
    # область документа читаем полосами крупно: MRZ и поля бланка целиком
    # на полном развороте теряются. На чётко прочитанной не-бланковой странице
    # полный проход уже видит всё — три дополнительных прохода ни к чему.
    identityish = page_has_identity_fields(best) or any(
        _is_mrz(normalize(ln.text)) for ln in best)
    if identityish or quality < 300:
        for band in ocr_bands(page):
            try:
                pix = page.get_pixmap(dpi=int(dpi * 2), clip=band, alpha=False)
            except Exception:
                continue
            with tempfile.TemporaryDirectory() as td:
                p = os.path.join(td, "band2.png")
                pix.save(p)
                rows = _ocr_tsv(p, exe, psm="6")
            for ln in _lines_from_tsv(rows, 72.0 / (dpi * 2)):
                for w in ln.words:
                    w.rect = pymupdf.Rect(w.rect.x0 + band.x0, w.rect.y0 + band.y0,
                                          w.rect.x1 + band.x0, w.rect.y1 + band.y0)
                    words.append(w)
    # повёрнутые на 90/270° строки (номер по краю бланка, вертикальные штампы):
    # это два полных прохода OCR, на хорошо читаемых страницах они не дают
    # ничего — разворачиваем только страницы, которые прочитались плохо
    if quality < 150:
        for angle in (90, 270):
            for w in ocr_rotated(page, exe, dpi, angle):
                words.append(w)
    return rows_from_words(_dedupe_words(words))


def ocr_rotated(page: pymupdf.Page, exe: str, dpi: int, angle: int) -> List[Word]:
    """Распознаёт страницу, повёрнутую на angle (90 или 270), и возвращает координаты назад."""
    step = int(dpi * 0.8)
    try:
        pix = page.get_pixmap(dpi=step, alpha=False)
    except Exception:
        return []
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).rotate(angle, expand=True)
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "rot.png")
        im.save(p)
        rows = _ocr_tsv(p, exe, psm="6")
    k = 72.0 / step
    W, H = pix.width * k, pix.height * k
    out: List[Word] = []
    for ln in _lines_from_tsv(rows, k):
        for w in ln.words:
            r = w.rect
            if angle == 90:
                w.rect = pymupdf.Rect(W - r.y1, r.x0, W - r.y0, r.x1)
            else:
                w.rect = pymupdf.Rect(r.y0, H - r.x1, r.y1, H - r.x0)
            out.append(w)
    return out


def ocr_bands(page: pymupdf.Page) -> List[pymupdf.Rect]:
    """Три горизонтальные полосы по области краски: верх, середина, низ."""
    rect, share = ink_bbox(page)
    if rect is None or share < 0.05:
        return []
    if rect.height < page.rect.height * 0.3:
        return []
    h = rect.height / 3.0
    out = []
    for i in range(3):
        y0 = rect.y0 + i * h - (4 if i else 0)
        y1 = rect.y0 + (i + 1) * h + (4 if i < 2 else 0)
        out.append(pymupdf.Rect(rect.x0, max(0, y0), rect.x1, min(page.rect.height, y1)))
    return out


def _dedupe_words(words: List[Word]) -> List[Word]:
    out: List[Word] = []
    for w in words:
        area = w.rect.get_area()
        if area <= 0:
            continue
        if any(w.rect.intersects(o.rect) and w.rect.intersect(o.rect).get_area() > 0.5 * area
               for o in out):
            continue
        out.append(w)
    return out


def unread_bands(page: pymupdf.Page, words: List[Word], limit: int = 4):
    """Пятна краски без распознанных слов: мелкий шрифт, печати, подписи.

    Tesseract при разборе всей страницы часто теряет мелкие блоки в углах.
    Сетка по странице ищет такие места, и они дочитываются отдельными фрагментами.
    """
    try:
        pix = page.get_pixmap(dpi=120, colorspace=pymupdf.csGRAY, alpha=False)
    except Exception:
        return []
    im = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    mask = im.point(lambda v: 255 if v < 170 else 0)
    d = ImageDraw.Draw(mask)
    sc = 120.0 / 72.0
    for w in words:
        r = w.rect
        d.rectangle([r.x0 * sc - 2, r.y0 * sc - 2, r.x1 * sc + 2, r.y1 * sc + 2], fill=0)

    cell = 26
    gw, gh = max(1, pix.width // cell), max(1, pix.height // cell)
    grid = mask.resize((gw, gh), Image.BOX).point(lambda v: 1 if v > 6 else 0)
    px = grid.load()
    seen = [[False] * gw for _ in range(gh)]
    spots: List[Tuple[int, pymupdf.Rect]] = []
    for gy in range(gh):
        for gx in range(gw):
            if px[gx, gy] == 0 or seen[gy][gx]:
                continue
            stack, cells = [(gx, gy)], []
            seen[gy][gx] = True
            while stack:
                cx, cy = stack.pop()
                cells.append((cx, cy))
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < gw and 0 <= ny < gh and px[nx, ny] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            if len(cells) < 3:
                continue
            xs = [c[0] for c in cells]
            ys = [c[1] for c in cells]
            box = pymupdf.Rect(min(xs) * cell / sc, min(ys) * cell / sc,
                               (max(xs) + 1) * cell / sc, (max(ys) + 1) * cell / sc)
            if box.width < 20 or box.height < 8:
                continue
            if box.width > page.rect.width * 0.75:
                continue
            spots.append((len(cells), box))
    spots.sort(key=lambda t: -t[0])
    return [b for _n, b in spots[:limit]]


def ocr_image(path: str, exe: str) -> List[Line]:
    return _lines_from_tsv(_ocr_tsv(path, exe), 1.0)


# --------------------------------------------------------------------------- #
#  Векторные линии
# --------------------------------------------------------------------------- #


def collect_segments(page: pymupdf.Page):
    segs = []
    for d in page.get_drawings():
        w = d.get("width") or 0.7
        color = d.get("color") or (0.0, 0.0, 0.0)
        if d.get("color") is None and d.get("fill") is not None:
            continue
        for it in d["items"]:
            if it[0] == "l":
                p1, p2 = it[1], it[2]
                segs.append((min(p1.x, p2.x), min(p1.y, p2.y), max(p1.x, p2.x), max(p1.y, p2.y), w, tuple(color)))
            elif it[0] == "re":
                r = it[1]
                segs += [(r.x0, r.y0, r.x1, r.y0, w, tuple(color)),
                         (r.x0, r.y1, r.x1, r.y1, w, tuple(color)),
                         (r.x0, r.y0, r.x0, r.y1, w, tuple(color)),
                         (r.x1, r.y0, r.x1, r.y1, w, tuple(color))]
    return segs


def restore_lines(page: pymupdf.Page, boxes: Sequence[pymupdf.Rect], segs) -> int:
    drawn = 0
    for x0, y0, x1, y1, w, color in segs:
        horiz = abs(y1 - y0) <= 0.8 and x1 - x0 > 1
        vert = abs(x1 - x0) <= 0.8 and y1 - y0 > 1
        if not horiz and not vert:
            continue
        if horiz:
            hit = [b for b in boxes if b.y0 - 1.5 <= y0 <= b.y1 + 3 and x1 >= b.x0 - 1 and x0 <= b.x1 + 1]
            if hit:
                a = max(x0, min(b.x0 for b in hit) - 1.5)
                c = min(x1, max(b.x1 for b in hit) + 1.5)
                if c > a:
                    page.draw_line((a, y0), (c, y0), color=color, width=w)
                    drawn += 1
        else:
            hit = [b for b in boxes if b.x0 - 1.5 <= x0 <= b.x1 + 1.5 and y1 >= b.y0 - 1 and y0 <= b.y1 + 1]
            if hit:
                a = max(y0, min(b.y0 for b in hit) - 1.5)
                c = min(y1, max(b.y1 for b in hit) + 1.5)
                if c > a:
                    page.draw_line((x0, a), (x0, c), color=color, width=w)
                    drawn += 1
    return drawn


# --------------------------------------------------------------------------- #
#  PDF
# --------------------------------------------------------------------------- #


def _page_lines_and_extras(page: pymupdf.Page, exe: Optional[str], ocr: str, dpi: int):
    text = page.get_text().strip()
    need_ocr = ocr == "always" or (ocr == "auto" and len(text) < 40)
    if need_ocr and exe:
        lines = ocr_page(page, exe, dpi)
        extras = [merge_upper_runs(lines)]
        words = [w for ln in lines for w in ln.words]
        return lines, extras, words, True
    lines = page_lines(page)
    extras = [page_blocks(page), merge_upper_runs(lines)]
    words = [Word(w, pymupdf.Rect(x0, y0, x1, y1))
             for x0, y0, x1, y1, w, *_ in page.get_text("words") if w.strip()]
    return lines, extras, words, False


def scan_pdf(src: str, mode: str, enabled: set, ocr: str, dpi: int, keep_ids: bool):
    """Только обнаружение, без изменения файла. Возвращает счётчики и боксы по страницам."""
    doc = pymupdf.open(src)
    exe = find_tesseract()
    per_page = []
    totals: Dict[str, int] = {}
    for page in doc:
        lines, extras, words, scanned = _page_lines_and_extras(page, exe, ocr, dpi)
        rects, stats = collect_hits(lines, enabled, mode, keep_ids=keep_ids, page_rect=page.rect)
        for extra in extras:
            r2, s2 = collect_hits(extra, enabled, mode, only_ids=BLOCK_RULES, keep_ids=keep_ids,
                                  page_rect=page.rect)
            rects += r2
            for k, v in s2.items():
                stats[k] = stats.get(k, 0) + v
        if "identity" in enabled:
            for pr in passport_pairs(words):
                rects.append((pr, "identity"))
                stats["identity"] = stats.get("identity", 0) + 1
        boxes = merge_rects([r for r, _ in rects])
        per_page.append((page.number, boxes, scanned))
        for k, v in stats.items():
            totals[k] = totals.get(k, 0) + v
    doc.close()
    return per_page, totals


def _hidden_font():
    """Шрифт с кириллицей для невидимого текстового слоя."""
    for p in (r"C:\Windows\Fonts\arial.ttf",
              r"C:\Windows\Fonts\DejaVuSans.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p):
            return p
    return None


def _insert_invisible_text(page, lines, boxes, report) -> int:
    """Кладёт слова OCR невидимым слоем (render_mode=3), пропуская закрытые плашками."""
    path = _hidden_font()
    if not path:
        report["text_layer_warning"] = "нет шрифта с кириллицей"
        return 0
    try:
        font = pymupdf.Font(fontfile=path)
    except Exception as err:
        report["text_layer_warning"] = f"шрифт не загрузился: {err}"
        return 0
    writer = pymupdf.TextWriter(page.rect)
    n = 0
    for line in lines:
        kept = [(w, w.text) for w in line.words
                if w.text.strip() and not any(w.rect.intersects(b) for b in boxes)]
        if not kept:
            continue
        heights = sorted(w.rect.height for w, _ in kept)
        h = heights[len(heights) // 2]
        baseline = pymupdf.Point(kept[0][0].rect.x0, max(w.rect.y1 for w, _ in kept) - 0.2 * h)
        text = " ".join(t for _, t in kept)
        try:
            writer.append(baseline, text, font=font, fontsize=h * 0.8)
            n += len(kept)
        except Exception:
            continue
    if n:
        writer.write_text(page, color=(0, 0, 0), render_mode=3)
    report["text_layer_words"] = report.get("text_layer_words", 0) + n
    return n


def _looks_like_code(im) -> bool:
    """Похоже на QR/штрихкод: почти чёрно-белая картинка с плотным рисунком."""
    g = im.convert("L")
    w, h = g.size
    if w < 24 or h < 24:
        return False
    hist = g.histogram()
    total = float(w * h)
    dark = sum(hist[:70]) / total
    light = sum(hist[186:]) / total
    if dark + light < 0.90:
        return False
    ratio = w / float(h)
    if 0.72 <= ratio <= 1.40 and dark >= 0.10:
        return True
    if ratio >= 2.0 and dark >= 0.12:
        return True
    return False


def _qr_scan_pixels(data, W: int, H: int, stride: int) -> List[Tuple[int, int, int, int]]:
    """Ищет QR (по трём маркерам 1:1:3:1:1) и штрихкоды (по повторяющимся вертикальным полосам).

    Возвращает список (x0, y0, x1, y1) в пикселях растра. Метод эвристический: ложные
    срабатывания отсекаются требованиями к квадратности области и числу маркеров.
    """
    if W < 100 or H < 100:
        return []

    def is_dark(x, y):
        return data[y * stride + x] < 128

    # --- QR: ищем строки с пропорцией 1:1:3:1:1 ---
    centers = []
    step = 2
    for y in range(0, H, step):
        runs = []
        prev = False
        start = 0
        for x in range(W):
            cur = is_dark(x, y)
            if cur != prev:
                if prev:
                    runs.append((start, x - 1, x - start))
                start = x
                prev = cur
        if prev:
            runs.append((start, W - 1, W - start))

        for i in range(len(runs) - 4):
            a, b, c, d, e = runs[i:i + 5]
            unit = (a[2] + b[2] + d[2] + e[2]) / 4.0
            if unit < 1.5 or unit > 22:
                continue
            if abs(a[2] - unit) > unit * 0.6 or abs(b[2] - unit) > unit * 0.6:
                continue
            if abs(c[2] - unit * 3) > unit * 1.2:
                continue
            if abs(d[2] - unit) > unit * 0.6 or abs(e[2] - unit) > unit * 0.6:
                continue
            centers.append(((a[0] + e[1]) // 2, y, unit * 7))

    # --- кластеризация маркеров ---
    groups = []
    for cx, cy, size in centers:
        for g in groups:
            if abs(g["x"] - cx) < size and abs(g["y"] - cy) < size:
                g["x"] = (g["x"] + cx) // 2
                g["y"] = (g["y"] + cy) // 2
                g["n"] += 1
                g["size"] = max(g["size"], size)
                break
        else:
            groups.append({"x": cx, "y": cy, "n": 1, "size": size})

    rects: List[Tuple[int, int, int, int]] = []
    solid = [g for g in groups if g["n"] >= 3]
    if len(solid) >= 3:
        xs = [g["x"] for g in solid]
        ys = [g["y"] for g in solid]
        pad = max(g["size"] for g in solid) * 0.9
        x0, x1 = min(xs) - pad, max(xs) + pad
        y0, y1 = min(ys) - pad, max(ys) + pad
        if x1 > x0 and y1 > y0 and 0.6 <= (x1 - x0) / float(y1 - y0) <= 1.7:
            rects.append((x0, y0, x1, y1))

    # --- штрихкод: одинаковый узор полос в соседних строках ---
    def row_signature(y):
        sig = []
        prev = False
        for x in range(W):
            cur = is_dark(x, y)
            if cur == prev:
                continue
            sig.append("1" if prev else "0")
            prev = cur
        return "".join(sig)

    prev_sig, streak, start_y = None, 0, 0
    for y in range(0, H, 2):
        sig = row_signature(y)
        if sig == prev_sig and 20 <= len(sig) <= 400:
            if streak == 0:
                start_y = y
            streak += 1
        else:
            if streak >= 40 and y - start_y < H * 0.25:
                rects.append((0, start_y, W, y))
            streak = 0
        prev_sig = sig
    if streak >= 40 and H - start_y < H * 0.25:
        rects.append((0, start_y, W, H))

    return rects


def _qr_scan_rects(page: pymupdf.Page, dpi: int = 200):
    """QR и штрихкоды на странице — в координатах страницы."""
    try:
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    except Exception:
        return []
    scale = page.rect.width / float(pix.width)
    return [pymupdf.Rect(x0 * scale, y0 * scale, x1 * scale, y1 * scale)
            for x0, y0, x1, y1 in _qr_scan_pixels(pix.samples, pix.width, pix.height, pix.stride)]


def photo_rects(page) -> List[pymupdf.Rect]:
    """Фотографии в документах: портретный растр, не код и не логотип."""
    out: List[pymupdf.Rect] = []
    page_area = page.rect.width * page.rect.height
    for img in page.get_images(full=True):
        xref = img[0]
        try:
            pix = pymupdf.Pixmap(page.parent, xref)
            if pix.n > 4:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        except Exception:
            continue
        w, h = im.size
        if not h:
            continue
        ratio = w / float(h)
        if not (0.55 <= ratio <= 1.55):
            continue
        if _looks_like_code(im):
            continue
        for r in page.get_image_rects(xref):
            share = (r.width * r.height) / page_area
            if 0.03 <= share <= 0.40:
                out.append(r)
    return out


def code_rects(page) -> List[pymupdf.Rect]:
    """Находит вставленные в страницу QR-коды и штрихкоды."""
    out: List[pymupdf.Rect] = []
    for img in page.get_images(full=True):
        xref = img[0]
        try:
            pix = pymupdf.Pixmap(page.parent, xref)
            if pix.n > 4:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        except Exception:
            continue
        if not _looks_like_code(im):
            continue
        for r in page.get_image_rects(xref):
            if r.width * r.height > page.rect.width * page.rect.height * 0.5:
                continue  # это сам скан страницы, а не вставленный код
            out.append(r)
    return out


def ink_bbox(page: pymupdf.Page, dpi: int = 100):
    """Границы краски на странице и её доля: где вообще что-то напечатано."""
    try:
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    except Exception:
        return None, 0.0
    im = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    mask = im.point(lambda v: 255 if v < 200 else 0)
    bbox = mask.getbbox()
    if not bbox:
        return None, 0.0
    sc = dpi / 72.0
    x0, y0, x1, y1 = bbox
    rect = pymupdf.Rect(x0 / sc, y0 / sc, x1 / sc, y1 / sc)
    dark = sum(mask.histogram()[255:])
    return rect, dark / float(pix.width * pix.height)


def passport_zones(page: pymupdf.Page, lines: List[Line], hint: bool = False) -> List[pymupdf.Rect]:
    """Разворот паспорта: закрываем зоны данных по геометрии бланка.

    На фото разворота OCR читает только обрывки, а данные расположены всегда одинаково:
    сверху — кем выдан и дата, ниже — фото и блок полей, внизу — машинная зона.
    Закрываем именно эти участки, а не весь лист.
    """
    try:
        pix = page.get_pixmap(dpi=100, colorspace=pymupdf.csGRAY, alpha=False)
    except Exception:
        return []
    im = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    bbox = im.point(lambda v: 255 if v < 150 else 0).getbbox()
    if not bbox:
        return []
    sc = 100.0 / 72.0
    rect = pymupdf.Rect(bbox[0] / sc, bbox[1] / sc, bbox[2] / sc, bbox[3] / sc)
    w, h = rect.width, rect.height
    if not (0.6 <= w / max(h, 1) <= 0.9):
        return []
    share = (w * h) / (page.rect.width * page.rect.height)
    if not (0.12 <= share <= 0.55):
        return []
    text = " ".join(normalize(ln.text) for ln in lines)
    low = text.lower()
    if not hint and not any(k in low for k in ("федерац", "паспорт", "российск")) \
            and "PN" not in text and "RUS" not in text:
        return []
    spans = [
        (0.03, 0.06, 0.97, 0.46),   # кем выдан, дата выдачи, код подразделения
        (0.04, 0.55, 0.43, 0.93),   # фотография
        (0.43, 0.54, 0.97, 0.90),   # фамилия, имя, отчество, дата и место рождения
        (0.03, 0.86, 0.99, 0.99),   # машинная зона MRZ
    ]
    return [pymupdf.Rect(rect.x0 + w * x0, rect.y0 + h * y0,
                         rect.x0 + w * x1, rect.y0 + h * y1) for x0, y0, x1, y1 in spans]


def passport_photo_rect(page: pymupdf.Page, lines: List[Line], hint: bool = False) -> Optional[pymupdf.Rect]:
    """Фотография на развороте паспорта: слева на странице данных.

    Привязка идёт к рамке самого бланка (плотное пятно краски), а не к машинной зоне:
    MRZ на плохих фото читается обрывком.
    """
    try:
        pix = page.get_pixmap(dpi=100, colorspace=pymupdf.csGRAY, alpha=False)
    except Exception:
        return None
    im = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    bbox = im.point(lambda v: 255 if v < 150 else 0).getbbox()
    if not bbox:
        return None
    sc = 100.0 / 72.0
    rect = pymupdf.Rect(bbox[0] / sc, bbox[1] / sc, bbox[2] / sc, bbox[3] / sc)
    w, h = rect.width, rect.height
    if not (0.5 <= w / max(h, 1) <= 1.0) or w * h < page.rect.width * page.rect.height * 0.05:
        return None
    return pymupdf.Rect(rect.x0 + w * 0.03, rect.y0 + h * 0.53,
                        rect.x0 + w * 0.44, rect.y0 + h * 0.95)


def render_pdf(src: str, dst: str, mode: str, enabled: set, ocr: str, dpi: int,
               keep_meta: bool, keep_ids: bool, progress=None):
    exe = find_tesseract()
    doc = pymupdf.open(src)
    report = {"file": os.path.basename(src), "mode": mode, "pages": doc.page_count,
              "categories": {}, "boxes": 0, "scanned_pages": 0, "restored_lines": 0}

    def analyze(pno: int):
        # свой handle на файл: PyMuPDF не потокобезопасен, а страницы анализируем
        # параллельно — Tesseract в это время крутится в подпроцессах
        d = pymupdf.open(src)
        page = d[pno]
        lines, extras, words, scanned = _page_lines_and_extras(page, exe, ocr, dpi)
        rects, stats = collect_hits(lines, enabled, mode, keep_ids=keep_ids, page_rect=page.rect)
        for extra in extras:
            r2, s2 = collect_hits(extra, enabled, mode, only_ids=BLOCK_RULES, keep_ids=keep_ids,
                                  page_rect=page.rect)
            rects += r2
            for k, v in s2.items():
                stats[k] = stats.get(k, 0) + v
        if "identity" in enabled:
            for pr in passport_pairs(words):
                rects.append((pr, "identity"))
                stats["identity"] = stats.get("identity", 0) + 1
        if "docno" in enabled:
            for r in code_rects(page):
                rects.append((r, "qr"))
                stats["qr"] = stats.get("qr", 0) + 1
            if scanned:
                for r in _qr_scan_rects(page, min(dpi, 150)):
                    rects.append((r, "qr"))
                    stats["qr"] = stats.get("qr", 0) + 1
        if ("identity" in enabled or "person" in enabled) and not keep_ids:
            for r in photo_rects(page):
                rects.append((r, "photos"))
                stats["photos"] = stats.get("photos", 0) + 1
            # на скане фотография — часть растра, отдельной картинкой её нет
            if scanned:
                for r in photo_blob_rects(page):
                    rects.append((r, "photos"))
                    stats["photos"] = stats.get("photos", 0) + 1
        limit = page.rect.width * page.rect.height * 0.25
        rects = [(r, c) for r, c in rects if r.width * r.height <= limit]
        boxes = merge_rects([r for r, _ in rects])
        face = None
        if scanned and "identity" in enabled and not keep_ids:
            face = passport_photo_rect(page, lines, "паспорт" in os.path.basename(src).lower())
        d.close()
        if progress:
            progress(pno + 1, doc.page_count)
        return lines, words, scanned, boxes, stats, face

    if doc.page_count > 1:
        # сканы обрабатываются в несколько потоков: OCR-прогоны независимы,
        # а последовательная обработка 9 страниц договора занимала 4+ минуты
        workers = min(4, (os.cpu_count() or 2), doc.page_count)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            analyzed = list(pool.map(analyze, range(doc.page_count)))
    else:
        analyzed = [analyze(0)]

    for page, (lines, words, scanned, boxes, stats, face) in zip(doc, analyzed):
        if face is not None:
            boxes = merge_rects(boxes + [face])
            report["photos"] = report.get("photos", 0) + 1
        report["boxes"] += len(boxes)
        for k, v in stats.items():
            report["categories"][k] = report["categories"].get(k, 0) + v

        if scanned:
            report["scanned_pages"] += 1
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            dr = ImageDraw.Draw(im)
            sc = pix.width / page.rect.width
            for b in boxes:
                dr.rectangle([b.x0 * sc, b.y0 * sc, b.x1 * sc, b.y1 * sc], fill=BAR_RGB, outline=None)
            if BAR_BORDER:
                for b in boxes:
                    dr.rectangle([b.x0 * sc, b.y0 * sc, b.x1 * sc, b.y1 * sc],
                                 outline=(110, 110, 110))
            rect = page.rect
            pno = page.number
            doc.delete_page(pno)
            new_page = doc.new_page(pno=pno, width=rect.width, height=rect.height)
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            new_page.insert_image(rect, stream=buf.getvalue())
            if TEXT_LAYER and lines:
                _insert_invisible_text(new_page, lines, boxes, report)
        else:
            segs = collect_segments(page)
            for b in boxes:
                page.add_redact_annot(b, fill=BAR_FILL, cross_out=False)
            if boxes:
                page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)
                if BAR_RGB == (255, 255, 255):
                    # на белой плашке возвращаем линии таблицы; на чёрной они были бы цветными
                    report["restored_lines"] += restore_lines(page, boxes, segs)
                if BAR_BORDER:
                    for b in boxes:
                        page.draw_rect(b, color=(0.55, 0.55, 0.55), width=0.6)
    if not keep_meta:
        doc.set_metadata({})
    try:
        doc.subset_fonts()
    except Exception:
        pass
    os.makedirs(os.path.dirname(os.path.abspath(dst)) or ".", exist_ok=True)
    doc.save(dst, garbage=4, deflate=True, clean=True)
    doc.close()
    return report


def verify_pdf(dst: str, enabled: set, mode: str) -> Dict[str, int]:
    """Повторная проверка готового файла: что в нём ещё находится."""
    doc = pymupdf.open(dst)
    lines: List[Line] = []
    for page in doc:
        for ln in lines_from_text(page.get_text()):
            text = normalize(ln.text).strip()
            # заголовок бланка («СВИДЕТЕЛЬСТВО О РОЖДЕНИИ») остаётся намеренно
            if len(text) < 80 and IDENT_DOC_RX.search(text):
                continue
            lines.append(ln)
    doc.close()
    _, stats = collect_hits(lines, enabled, mode)
    return stats


# --------------------------------------------------------------------------- #
#  Изображения
# --------------------------------------------------------------------------- #


def _save_image(im: Image.Image, dst: str, fmt: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(dst)) or ".", exist_ok=True)
    if fmt == "JPG":
        fmt = "JPEG"
    out_fmt = fmt if fmt in ("PNG", "JPEG", "TIFF", "BMP", "WEBP") else "PNG"
    save_kwargs = {}
    if out_fmt == "JPEG":
        save_kwargs = {"quality": 95, "subsampling": 0}
    im.save(dst, format=out_fmt, **save_kwargs)


def render_image(src: str, dst: Optional[str], mode: str, enabled: set, keep_ids: bool,
                 save: bool = True):
    exe = find_tesseract()
    if not exe:
        raise RuntimeError("Tesseract не найден: изображения без OCR не обезличить")
    im = Image.open(src)
    fmt = (im.format or "PNG").upper()
    size = pymupdf.Rect(0, 0, im.width, im.height)
    lines = ocr_image(src, exe)
    full = " ".join(ln.text for ln in lines)
    if mode == "auto":
        mode = "judicial" if is_court_act(full) else "document"
    if mode == "judicial" and enabled == set(CATEGORIES):
        enabled = set(enabled) - JUDICIAL_KEEP
    CURRENT["enabled"], CURRENT["mode"] = enabled, mode
    rects, stats = collect_hits(lines, enabled, mode, keep_ids=keep_ids, page_rect=size)
    for extra in (merge_upper_runs(lines),):
        r2, s2 = collect_hits(extra, enabled, mode, only_ids=BLOCK_RULES, keep_ids=keep_ids,
                              page_rect=size)
        rects += r2
        for k, v in s2.items():
            stats[k] = stats.get(k, 0) + v
    if "docno" in enabled:
        # QR-коды и штрихкоды в растре — тем же сканером, что для сканов PDF
        g = im.convert("L")
        for x0, y0, x1, y1 in _qr_scan_pixels(g.tobytes(), g.width, g.height, g.width):
            rects.append((pymupdf.Rect(x0, y0, x1, y1), "qr"))
            stats["qr"] = stats.get("qr", 0) + 1
    if "identity" in enabled:
        for pr in passport_pairs([w for ln in lines for w in ln.words]):
            rects.append((pr, "identity"))
            stats["identity"] = stats.get("identity", 0) + 1
        if not keep_ids and page_has_identity_fields(lines):
            for r in blob_rects_in_image(im, size):
                rects.append((r, "identity"))
                stats["identity"] = stats.get("identity", 0) + 1
    boxes = merge_rects([r for r, _ in rects])

    im = im.convert("RGB")
    dr = ImageDraw.Draw(im)
    for b in boxes:
        dr.rectangle([b.x0, b.y0, b.x1, b.y1], fill=BAR_RGB, outline=None)
    if BAR_BORDER:
        for b in boxes:
            dr.rectangle([b.x0, b.y0, b.x1, b.y1], outline=(110, 110, 110))
    rep = {"file": os.path.basename(src), "mode": mode, "categories": stats,
           "boxes": len(boxes), "size": list(im.size), "enabled": enabled}
    if not save:
        rep["_image"] = im
        rep["_fmt"] = fmt
        return rep
    _save_image(im, dst, fmt)
    rep["output"] = dst
    return rep


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


def anonymize_text(text: str, mode: str, enabled: set, keep_ids: bool):
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
        if mode == "judicial" and keep_ids and rid in ID_RULES:
            continue
        text = sub(cat, rid, re.compile(pat, re.I), val, grp)

    tagged: List[Tuple[int, int, str]] = []
    items = lines_with_offsets(text)
    aux = [ln for _p, ln in items]
    if "person" in enabled:
        for base, ln in items:
            tagged.extend((base + a, base + b, "person")
                          for a, b in find_names(ln) + english_names(ln))
        # ФИО, разорванное по строкам: «Иванов» / «Иван Иванович»
        for li, a, b in cross_line_name_spans(aux):
            tagged.append((items[li][0] + a, items[li][0] + b, "person"))
        # одиночные имена и отчества — как в PDF-пайплайне
        for m in re.finditer(r"(?<![А-Яа-яЁё])([А-ЯЁ][А-Яа-яЁё\-]{2,})(?![А-Яа-яЁё])", text):
            low = _cyr(m.group(1)).lower()
            if _given_like(low) or PATRONYMIC.search(low):
                tagged.append((m.start(1), m.end(1), "person"))
    if "contacts" in enabled:
        for li, a, b in cross_line_contact_spans(aux):
            tagged.append((items[li][0] + a, items[li][0] + b, "contacts"))
    if tagged:
        tagged.sort()
        merged: List[Tuple[int, int, str]] = []
        for a, b, cat in tagged:
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b), merged[-1][2])
            else:
                merged.append((a, b, cat))
        parts, cur = [], 0
        for a, b, cat in merged:
            parts.append(text[cur:a])
            parts.append(PLACEHOLDER)
            cur = b
            stats[cat] = stats.get(cat, 0) + 1
        parts.append(text[cur:])
        text = "".join(parts)
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
        if mode == "judicial" and rid in ID_RULES:
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


def process(path: str, args, enabled: Optional[set] = None):
    ext = os.path.splitext(path)[1].lower()
    base, _ = os.path.splitext(os.path.basename(path))

    if ext == ".pdf":
        doc = pymupdf.open(path)
        full = "".join(p.get_text() for p in doc)
        doc.close()
        mode = args.mode
        if mode == "auto":
            mode = "judicial" if is_court_act(full) else "document"
        enabled = set(enabled) if enabled is not None else default_enabled(mode, args)
        if args.ask:
            _, found = scan_pdf(path, mode, enabled, args.ocr, args.dpi, args.court_keep_ids)
            enabled = ask_categories(found)
        CURRENT["enabled"], CURRENT["mode"] = enabled, mode
        dst = args.out or out_name(path, ".pdf")
        if not dst.lower().endswith(".pdf"):
            fixed = os.path.splitext(dst)[0] + ".pdf"
            print(f"PDF сохраняется только в PDF: {dst} -> {fixed}", file=sys.stderr)
            dst = fixed
        rep = render_pdf(path, dst, mode, enabled, args.ocr, args.dpi, args.keep_meta, args.court_keep_ids)
        left = verify_pdf(dst, enabled, mode)
        rep["mode"] = mode
        rep["kept_categories"] = sorted(set(CATEGORIES) - enabled)
        rep["leftover"] = left
        rep["output"] = dst
        return rep

    if ext in IMAGE_EXT:
        enabled = set(enabled) if enabled is not None else default_enabled(args.mode, args)
        # режим определяется по OCR внутри render_image; имя результата считаем после
        rep = render_image(path, None, args.mode, enabled, args.court_keep_ids, save=False)
        CURRENT["enabled"], CURRENT["mode"] = rep["enabled"], rep["mode"]
        dst = args.out or out_name(path, ext)
        _save_image(rep.pop("_image"), dst, rep.pop("_fmt"))
        rep["kept_categories"] = sorted(set(CATEGORIES) - set(CURRENT["enabled"]))
        rep["output"] = dst
        return rep

    if ext in (".docx", ".doc"):
        if ext == ".doc":
            with open(path, "rb") as fh:
                magic = fh.read(8)
            if magic.startswith(b"\xd0\xcf\x11\xe0") or magic.startswith(b"{\\rtf"):
                raise SystemExit("устаревший формат .doc: пересохраните файл как .docx или PDF")
        md = docx_to_md(path)
        mode = args.mode
        if mode == "auto":
            mode = "judicial" if is_court_act(md) else "document"
        enabled = set(enabled) if enabled is not None else default_enabled(mode, args)
        if args.ask:
            _, found = collect_hits(lines_from_text(md), enabled, mode)
            enabled = ask_categories(found)
        anon, stats = anonymize_text(md, mode, enabled, args.court_keep_ids)
        CURRENT["enabled"], CURRENT["mode"] = enabled, mode
        dst = args.out or out_name(path, ".md")
        with open(dst, "w", encoding="utf-8") as f:
            f.write(anon)
        return {"file": os.path.basename(path), "mode": mode, "converted_to_md": True,
                "categories": stats, "kept_categories": sorted(set(CATEGORIES) - enabled),
                "output": dst}

    if ext in (".md", ".markdown", ".txt"):
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        mode = args.mode
        if mode == "auto":
            mode = "judicial" if is_court_act(raw) else "document"
        enabled = set(enabled) if enabled is not None else default_enabled(mode, args)
        if args.ask:
            _, found = collect_hits(lines_from_text(raw), enabled, mode)
            enabled = ask_categories(found)
        anon, stats = anonymize_text(raw, mode, enabled, args.court_keep_ids)
        CURRENT["enabled"], CURRENT["mode"] = enabled, mode
        dst = args.out or out_name(path, ext)
        with open(dst, "w", encoding="utf-8") as f:
            f.write(anon)
        return {"file": os.path.basename(path), "mode": mode, "categories": stats,
                "kept_categories": sorted(set(CATEGORIES) - enabled), "output": dst}

    raise SystemExit(f"Неподдерживаемый формат: {ext}")


def default_enabled(mode: str, args) -> set:
    enabled = set(CATEGORIES)
    if mode == "judicial":
        enabled -= JUDICIAL_KEEP
        if args.court_keep_ids:
            pass
    if args.only:
        enabled &= set(args.only)
    if args.keep:
        enabled -= set(args.keep)
    return enabled


def main(argv: Optional[Sequence[str]] = None) -> int:
    global PLACEHOLDER, BAR_BORDER, TEXT_LAYER, KEEP_NAME, BAR_RGB, BAR_FILL
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
    ap.add_argument("--ocr", default="auto", choices=["auto", "always", "never"])
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--placeholder", default=PLACEHOLDER)
    ap.add_argument("--no-text-layer", action="store_true",
                    help="не добавлять невидимый текстовый слой в сканы")
    ap.add_argument("--no-bar-border", action="store_true",
                    help="плашки без серой обводки (только заливка)")
    ap.add_argument("--white-bars", action="store_true",
                    help="белые плашки вместо чёрных (с восстановлением линий таблиц)")
    ap.add_argument("--keep-name", action="store_true",
                    help="сохранить исходное название файла в имени результата")
    ap.add_argument("--safe-name", action="store_true",
                    help="оставлено для совместимости: имена и так обезличиваются")
    ap.add_argument("--keep-meta", action="store_true")
    ap.add_argument("--court-keep-ids", action="store_true",
                    help="в судебных актах не скрывать ИНН/ОГРН/СНИЛС/паспорт")
    ap.add_argument("--report", help="записать JSON-отчёт сюда")
    ap.add_argument("--json", action="store_true", help="печатать только JSON")
    args = ap.parse_args(argv)

    if args.list_categories:
        print_categories()
        return 0

    PLACEHOLDER = args.placeholder
    KEEP_NAME = args.keep_name
    BAR_BORDER = not args.no_bar_border
    TEXT_LAYER = not args.no_text_layer
    if args.white_bars:
        BAR_RGB = (255, 255, 255)
        BAR_FILL = (1.0, 1.0, 1.0)

    files: List[str] = []
    for item in args.inputs:
        if os.path.isdir(item):
            for name in sorted(os.listdir(item)):
                if os.path.splitext(name)[1].lower() in ({".pdf", ".docx", ".doc", ".md", ".markdown",
                                                          ".txt"} | IMAGE_EXT):
                    files.append(os.path.join(item, name))
        elif any(ch in item for ch in "*?["):
            files.extend(sorted(glob.glob(item)))
        else:
            files.append(item)
    if not files:
        ap.error("не указаны входные файлы")

    results = []
    enabled = None
    missing = 0
    for path in files:
        if not os.path.exists(path):
            print(f"нет файла: {path}", file=sys.stderr)
            missing += 1
            continue
        try:
            rep = process(path, args, enabled)
        except SystemExit as err:
            # один неподдерживаемый файл не должен срывать всю пачку
            print(f"пропущен {path}: {err}", file=sys.stderr)
            missing += 1
            continue
        except Exception as err:  # noqa: BLE001
            print(f"ошибка {path}: {type(err).__name__}: {err}", file=sys.stderr)
            missing += 1
            continue
        if args.keep is not None and enabled is None and (args.keep or args.only):
            enabled = default_enabled(rep.get("mode", args.mode), args)
        results.append(rep)
        if not args.json:
            cats = ", ".join(f"{k}:{v}" for k, v in sorted(rep.get("categories", {}).items())) or "—"
            print(f"{rep['file']} -> {rep['output']}")

    if not results:
        print("ни одного файла не обработано", file=sys.stderr)
        return 2

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
