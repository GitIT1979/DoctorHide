# -*- coding: utf-8 -*-
"""Глобальные настройки и константы DoctorHide."""

from __future__ import annotations


class SkipFile(Exception):
    """Файл пропущен (неподдерживаемый формат, скан без текста и т.п.)."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


PLACEHOLDER = "[censored]"
SAFE_NAME = False
KEEP_NAME = False
KEEP_FORMAT = False
NAME_COUNTER: dict = {}
CURRENT: dict = {"enabled": None, "mode": None}

BAR_RGB = (0, 0, 0)
BAR_FILL = (0, 0, 0)

TEXT_EXT = {".txt", ".md", ".markdown"}
SHEET_EXT = {".xlsx", ".xls"}
DOC_EXT = {".docx", ".doc", ".rtf"}  # .doc/.rtf → через LibreOffice в текст/docx
SUPPORTED_EXT = TEXT_EXT | SHEET_EXT | DOC_EXT | {".pdf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
