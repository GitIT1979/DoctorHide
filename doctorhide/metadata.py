# -*- coding: utf-8 -*-
"""Очистка метаданных файлов: EXIF/GPS в изображениях, метаданные PDF,
core.xml/app.xml в DOCX/XLSX.

Функции:
    clear_pdf_metadata(doc) -> None
        Полная очистка PDF: doc.info (set_metadata({})), XMP stream,
        Information Dictionary.

    clear_office_metadata(path) -> None
        Очистка docProps/core.xml и docProps/app.xml в .docx/.xlsx (ZIP).
        Удаляет docProps/custom.xml полностью.

    strip_image_exif(src, dst=None) -> bool
        Удаляет EXIF/GPS/XMP/IPTC из JPEG/TIFF/WebP/PNG через PIL.

    extract_image_exif_text(src) -> str
        Извлекает PII-релевантные EXIF-поля как текст для анонимизации:
        GPS-координаты (decimal degrees), DateTimeOriginal, Artist,
        Copyright, BodySerialNumber и др. Возвращает пустую строку,
        если EXIF нет или PIL не установлен.
"""

from __future__ import annotations

import os
import zipfile


def clear_pdf_metadata(doc) -> None:
    """Полная очистка метаданных PDF: doc.info + XMP + Information Dictionary.

    Заменяет простое doc.set_metadata({}) на более тщательную очистку:
    дополнительно удаляет XMP metadata stream и обнуляет поля
    Information Dictionary (legacy).
    """
    # 1. doc.info — стандартные поля (Author, Title, Subject, Keywords,
    #    Creator, Producer, CreationDate, ModDate, Trapped).
    try:
        doc.set_metadata({})
    except Exception:
        pass

    # 2. XMP metadata stream — отдельный поток, на который указывает
    #    /Metadata в каталоге. Удаляем ссылку и сам поток.
    try:
        catalog_xref = doc.pdf_catalog()
        md_ref = doc.xref_get_key(catalog_xref, "Metadata")
        if md_ref and md_ref[0] == "xref":
            md_num = int(md_ref[1].split()[0])
            if hasattr(doc, "delete_xref"):
                doc.delete_xref(md_num)
            doc.xref_set_key(catalog_xref, "Metadata", "null")
    except Exception:
        pass

    # 3. Information Dictionary (legacy) — обнуляем поля поимённо.
    try:
        info_ref = doc.xref_get_key(doc.pdf_catalog(), "Info")
        if info_ref and info_ref[0] == "xref":
            info_num = int(info_ref[1].split()[0])
            for field in ("Author", "Title", "Subject", "Keywords",
                          "Creator", "Producer", "CreationDate", "ModDate"):
                try:
                    doc.xref_set_key(info_num, field, "null")
                except Exception:
                    pass
    except Exception:
        pass


def clear_office_metadata(path: str) -> None:
    """Очистка метаданных в .docx/.xlsx (ZIP-архивы OOXML).

    docProps/core.xml: creator, lastModifiedBy, created, modified,
    title, subject, description, category, version, keywords.
    docProps/app.xml: Application, AppVersion, Company, Manager, TitlesOfParts.
    docProps/custom.xml: пользовательские свойства — удаляется полностью.
    """
    if not os.path.exists(path):
        return

    empty_core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"/>'
    )
    empty_app = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"/>'
    )

    tmp_path = path + ".meta.tmp"
    try:
        with zipfile.ZipFile(path, "r") as zin:
            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    data = zin.read(item.filename)
                    if item.filename == "docProps/core.xml":
                        data = empty_core.encode("utf-8")
                    elif item.filename == "docProps/app.xml":
                        data = empty_app.encode("utf-8")
                    elif item.filename == "docProps/custom.xml":
                        continue  # удаляем полностью
                    zout.writestr(item, data)
        os.replace(tmp_path, path)
    except (zipfile.BadZipFile, OSError):
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def strip_image_exif(src: str, dst: str | None = None) -> bool:
    """Удаляет EXIF/GPS/XMP/IPTC из JPEG/TIFF/WebP/PNG через PIL.

    dst=None — перезаписать src. Возвращает True при успехе.
    """
    try:
        from PIL import Image
    except ImportError:
        return False

    dst_path = dst or src
    try:
        with Image.open(src) as img:
            save_kwargs = {}
            fmt = img.format
            if fmt == "PNG":
                from PIL.PngImagePlugin import PngInfo
                save_kwargs["pnginfo"] = PngInfo()
            elif fmt in ("JPEG", "JPG"):
                save_kwargs["exif"] = b""
                save_kwargs["xmp"] = b""
            elif fmt == "WEBP":
                save_kwargs["exif"] = b""
                save_kwargs["xmp"] = b""
            elif fmt in ("TIFF", "TIF"):
                save_kwargs["tiffinfo"] = {}
            img.save(dst_path, format=fmt, **save_kwargs)
        return True
    except Exception:
        return False


# EXIF-теги с PII-релевантными данными.
# Полный список: https://exiftool.org/TagNames/EXIF.html
_EXIF_PII_TAGS = {
    0x010F: "Make",
    0x0110: "Model",
    0x0131: "Software",
    0x013B: "Artist",
    0x8298: "Copyright",
    0x9003: "DateTimeOriginal",
    0x9004: "DateTimeDigitized",
    0x9286: "UserComment",
    0x9C9B: "XPTitle",
    0x9C9D: "XPAuthor",
    0x9C9E: "XPKeywords",
    0x9CA0: "XPComment",
    0x9CA1: "XPSubject",
    # Теги из ExifIFD (доступ через get_ifd(0x8769)):
    0xC62F: "BodySerialNumber",       # серийный номер камеры
    0xC631: "LensSpecification",
    0xC632: "LensModel",
    0xC634: "LensSerialNumber",       # серийный номер объектива
    # Теги из IFD0 (основной):
    0xA430: "CameraOwnerName",        # владелец камеры
    0xA431: "BodySerialNumber",       # альтернативный тег серийника
    0xA432: "LensSpecification",
    0xA433: "LensModel",
    0xA435: "LensSerialNumber",       # альтернативный тег
}


def extract_image_exif_text(src: str) -> str:
    """Извлекает PII-релевантные EXIF-поля как текст для анонимизации.

    Возвращает строку вида:
        [EXIF Metadata]
        Make: Apple
        Model: iPhone 13 Pro
        DateTimeOriginal: 2024:05:15 12:34:56
        GPSLatitude: 55.755800 N
        GPSLongitude: 37.617300 E
        Artist: Ivan Ivanov

    Пустая строка — если EXIF нет или PIL не установлен.
    """
    try:
        from PIL import Image
    except ImportError:
        return ""

    lines: list[str] = []
    try:
        with Image.open(src) as img:
            exif = img.getexif()
            if not exif:
                return ""

            lines.append("[EXIF Metadata]")

            # Основной IFD + ExifIFD (под-IFD для DateTimeOriginal, BodySerialNumber).
            # PIL getexif() в новых версиях включает под-IFD, но для надёжности
            # проверяем и ExifIFD отдельно.
            exif_ifd = exif.get_ifd(0x8769) if hasattr(exif, "get_ifd") else {}
            all_tags = dict(exif)
            if exif_ifd:
                all_tags.update(exif_ifd)

            for tag_id, label in _EXIF_PII_TAGS.items():
                val = all_tags.get(tag_id)
                if val:
                    if isinstance(val, bytes):
                        try:
                            val = val.decode("utf-8", errors="replace")
                        except Exception:
                            continue
                    lines.append(f"{label}: {val}")

            # GPS IFD — отдельный подсловарь.
            gps_ifd = exif.get_ifd(0x8825) if hasattr(exif, "get_ifd") else {}
            if gps_ifd:
                lat_raw = gps_ifd.get(0x0002) or gps_ifd.get(2)
                lat_ref = gps_ifd.get(0x0001) or gps_ifd.get(1) or ""
                lat = _gps_coord_to_decimal(lat_raw, lat_ref)
                lon_raw = gps_ifd.get(0x0004) or gps_ifd.get(4)
                lon_ref = gps_ifd.get(0x0003) or gps_ifd.get(3) or ""
                lon = _gps_coord_to_decimal(lon_raw, lon_ref)
                if isinstance(lat_ref, bytes):
                    lat_ref = lat_ref.decode("ascii", errors="replace").strip()
                if isinstance(lon_ref, bytes):
                    lon_ref = lon_ref.decode("ascii", errors="replace").strip()
                if lat is not None:
                    lines.append(f"GPSLatitude: {lat:.6f} {lat_ref}".strip())
                if lon is not None:
                    lines.append(f"GPSLongitude: {lon:.6f} {lon_ref}".strip())
                alt = gps_ifd.get(0x0006) or gps_ifd.get(6)
                if alt is not None:
                    alt_f = _rational_to_float(alt)
                    if alt_f is None and isinstance(alt, (int, float)):
                        alt_f = float(alt)
                    if alt_f is not None:
                        lines.append(f"GPSAltitude: {alt_f:.1f} m")
                ts_raw = gps_ifd.get(0x0007) or gps_ifd.get(7)
                ts_str = _format_gps_time(ts_raw)
                if ts_str:
                    lines.append(f"GPSTimeStamp: {ts_str}")
                ds = gps_ifd.get(0x001D) or gps_ifd.get(29)
                if ds:
                    if isinstance(ds, bytes):
                        ds = ds.decode("ascii", errors="replace")
                    lines.append(f"GPSDateStamp: {ds}")

            if len(lines) <= 1:
                return ""
    except Exception:
        return ""

    return "\n".join(lines) + "\n"


def _gps_coord_to_decimal(rational_tuple, ref) -> float | None:
    """Конвертирует EXIF GPS в decimal degrees. Поддерживает форматы PIL и piexif.

    PIL может возвращать IFDRational объекты — у них есть __float__,
    поэтому используем float() напрямую вместо isinstance.
    """
    if not rational_tuple or not isinstance(rational_tuple, (tuple, list)):
        return None
    try:
        if len(rational_tuple) < 3:
            return None
        # PIL: (55.0, 45.0, 21.0) где элементы — IFDRational
        # piexif: ((55,1), (45,1), (21,1)) где элементы — кортежи (num, den)
        first = rational_tuple[0]
        if isinstance(first, (tuple, list)) and len(first) == 2:
            d = _rational_to_float(rational_tuple[0])
            m = _rational_to_float(rational_tuple[1])
            s = _rational_to_float(rational_tuple[2])
        else:
            # IFDRational, int, float — у всех есть __float__
            d = float(first)
            m = float(rational_tuple[1])
            s = float(rational_tuple[2])
        decimal = d + m / 60.0 + s / 3600.0
        if isinstance(ref, bytes):
            ref = ref.decode("ascii", errors="replace").strip()
        if ref in ("S", "W"):
            decimal = -decimal
        return decimal
    except (IndexError, TypeError, ValueError, ZeroDivisionError):
        return None


def _format_gps_time(value) -> str:
    """Форматирует GPSTimeStamp в 'HH:MM:SS'."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("ascii")
        except Exception:
            return ""
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        parts = []
        for v in value[:3]:
            f = _rational_to_float(v)
            if f is None:
                return ""
            parts.append(int(f))
        try:
            return "{:02d}:{:02d}:{:02d}".format(*parts)
        except Exception:
            return ""
    return ""


def _rational_to_float(value) -> float | None:
    """Конвертирует EXIF Rational в float. Поддерживает int, float, IFDRational,
    и кортеж (num, den) из piexif."""
    try:
        if isinstance(value, (tuple, list)) and len(value) == 2:
            num, den = value
            return float(num) / float(den) if den else 0.0
        return float(value)  # работает для int, float, IFDRational
    except (TypeError, ValueError, ZeroDivisionError):
        return None


__all__ = [
    "clear_pdf_metadata",
    "clear_office_metadata",
    "strip_image_exif",
    "extract_image_exif_text",
]
