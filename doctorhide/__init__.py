# -*- coding: utf-8 -*-
"""DoctorHide — пакет обезличивания документов."""

from .config import (
    SkipFile,
    PLACEHOLDER, SAFE_NAME, KEEP_NAME, KEEP_FORMAT, NAME_COUNTER, CURRENT,
    BAR_RGB, BAR_FILL, TEXT_EXT, SHEET_EXT, DOC_EXT, SUPPORTED_EXT, IMAGE_EXT,
)
from .categories import CATEGORIES, JUDICIAL_KEEP
from .text_model import (
    Rect, Word, Line, _group_lines, _is_upperish, lines_from_text, lines_with_offsets,
)
from .names import (
    GIVEN_NAMES, PATRONYMIC, SURNAME, SURNAME_STRICT, SURNAME_CASE, NAME_STOP, CAPS_HYPHEN_STOP,
    NAME_MARKERS, HOMO, _cyr, normalize, find_names, english_names, grow_spans,
)
from .rules import (
    CITY_STEMS, EN_CITIES, STREET, _CITY, RULES, BLOCK_RULES, COURT_MARKERS,
    inn10_ok, inn12_ok, ogrn_ok, validate, FIELD_LABELS, FIELD_LABEL_RX,
    label_category, is_court_act,
)
from .finance import is_finance_token, is_sensitive_number, mask_numbers
from .detect import find_hits, collect_hits, pdf_hits
from .table_layout import detect_table_blocks, line_table_flags
from .vertical_text import is_vertical_word, merge_vertical_words
from .anonymize import anonymize_text
from .converters import docx_to_md, sheet_to_md, anonymize_docx, anonymize_xlsx
from .pdf import pdf_lines, bars, redact_pdf
from .process import (
    sanitize_filename, output_name, out_name, default_enabled, process,
)
from .metadata import (
    clear_pdf_metadata, clear_office_metadata,
    strip_image_exif, extract_image_exif_text,
)
from .cli import print_categories, ask_categories, main

__all__ = [
    "CATEGORIES", "JUDICIAL_KEEP",
    "Rect", "Word", "Line", "_group_lines", "_is_upperish", "lines_from_text", "lines_with_offsets",
    "GIVEN_NAMES", "PATRONYMIC", "SURNAME", "SURNAME_STRICT", "SURNAME_CASE", "NAME_STOP", "CAPS_HYPHEN_STOP",
    "NAME_MARKERS", "HOMO", "_cyr", "normalize", "find_names", "english_names", "grow_spans",
    "CITY_STEMS", "EN_CITIES", "STREET", "_CITY", "RULES", "BLOCK_RULES", "COURT_MARKERS",
    "inn10_ok", "inn12_ok", "ogrn_ok", "validate", "FIELD_LABELS", "FIELD_LABEL_RX",
    "label_category", "is_court_act",
    "is_finance_token", "is_sensitive_number", "mask_numbers",
    "find_hits", "collect_hits", "pdf_hits",
    "detect_table_blocks", "line_table_flags",
    "is_vertical_word", "merge_vertical_words",
    "anonymize_text", "docx_to_md", "sheet_to_md", "anonymize_docx", "anonymize_xlsx",
    "pdf_lines", "bars", "redact_pdf",
    "SkipFile",
    "PLACEHOLDER", "SAFE_NAME", "KEEP_NAME", "KEEP_FORMAT", "NAME_COUNTER", "CURRENT",
    "BAR_RGB", "BAR_FILL", "TEXT_EXT", "SHEET_EXT", "DOC_EXT", "SUPPORTED_EXT", "IMAGE_EXT",
    "sanitize_filename", "output_name", "out_name", "default_enabled", "process",
    "clear_pdf_metadata", "clear_office_metadata",
    "strip_image_exif", "extract_image_exif_text",
    "print_categories", "ask_categories", "main",
]

__version__ = "0.14.3-dev"
