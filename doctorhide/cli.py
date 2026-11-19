# -*- coding: utf-8 -*-
"""CLI DoctorHide: argparse, отчёты, точка входа main()."""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from typing import Dict, List, Optional, Sequence

from . import config
from .config import SkipFile, SUPPORTED_EXT, IMAGE_EXT
from .categories import CATEGORIES
from .process import process, default_enabled


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


def main(argv: Optional[Sequence[str]] = None) -> int:
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
    ap.add_argument("--placeholder", default=config.PLACEHOLDER)
    ap.add_argument("--keep-name", action="store_true",
                    help="сохранить исходное название файла в имени результата")
    ap.add_argument("--safe-name", action="store_true",
                    help="совместимость: имена и так обезличиваются")
    ap.add_argument("--keep-format", action="store_true",
                    help="для DOCX/XLSX: сохранить исходный формат (.docx/.xlsx) с [censored]")
    ap.add_argument("--keep-meta", action="store_true")
    ap.add_argument("--report", help="записать JSON-отчёт сюда")
    ap.add_argument("--json", action="store_true", help="печатать только JSON")
    args = ap.parse_args(argv)

    if args.list_categories:
        print_categories()
        return 0

    config.PLACEHOLDER = args.placeholder
    config.SAFE_NAME = args.safe_name
    config.KEEP_NAME = args.keep_name
    config.KEEP_FORMAT = args.keep_format

    files: List[str] = []
    # Включаем изображения, если установлен OCR.
    from .ocr import available as ocr_available
    accept_ext = set(SUPPORTED_EXT)
    if ocr_available():
        accept_ext |= IMAGE_EXT
    for item in args.inputs:
        if os.path.isdir(item):
            for name in sorted(os.listdir(item)):
                ext = os.path.splitext(name)[1].lower()
                if ext in accept_ext:
                    files.append(os.path.join(item, name))
        elif any(ch in item for ch in "*?["):
            files.extend(sorted(glob.glob(item)))
        else:
            files.append(item)
    if not files:
        ap.error("не указаны входные файлы")

    results = []
    enabled = None
    config.CURRENT["outputs"] = set()
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
                cats = ", ".join(
                    f"{k}:{v}" for k, v in sorted(rep.get("categories", {}).items())
                ) or "—"
                print(f"{rep['file']} -> {rep['output']}  [{cats}]")
        except SkipFile as err:
            rec = {
                "file": os.path.basename(path),
                "status": "skipped",
                "reason": str(err.reason if hasattr(err, "reason") else err),
                "output": None,
            }
            results.append(rec)
            if not args.json:
                print(f"пропущен {path}: {rec['reason']}", file=sys.stderr)
        except Exception as err:  # noqa: BLE001
            rec = {
                "file": os.path.basename(path),
                "status": "error",
                "reason": f"{type(err).__name__}: {err}",
                "output": None,
            }
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
