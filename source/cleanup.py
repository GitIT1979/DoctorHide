#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Очистка рабочей папки DoctorHide от артефактов.

Удаляет:
  - _gui_work/     — вход/выход GUI, сломанные результаты
  - _private/      — демо, диагностика, промежуточные PDF
  - _tools/        — tessdata (OCR больше не используется)
  - tests/fixtures/ — сканы для OCR-тестов
  - examples/      — примеры PDF
  - legacy/        — старый anonymize_v1.py
  - Договор_hidden.md — результат работы

Не трогает:
  - doctorhide/    — пакет
  - doctorhide.py  — лаунчер
  - tests/*.py     — тесты
  - README.md, requirements.txt, .gitignore
  - gui/           — веб-интерфейс
  - ПРАВИЛА_АНОНИМИЗАЦИИ.md, Запустить DoctorHide.cmd, КАК_ОБНОВИТЬ.txt

Использование:
  python cleanup.py           # показать, что будет удалено
  python cleanup.py --yes     # удалить
  python cleanup.py --yes --all  # удалить ещё и gui/ + служебные txt/cmd
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Основной набор (рабочие артефакты)
TARGETS = [
    "_gui_work",
    "_private",
    "_tools",
    "tests/fixtures",
    "examples",
    "legacy",
    "Договор_hidden.md",
]

# Дополнительно при --all
TARGETS_ALL = [
    "gui",
    "Запустить DoctorHide.cmd",
    "КАК_ОБНОВИТЬ.txt",
]


def size_of(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"


def main() -> int:
    ap = argparse.ArgumentParser(description="Очистка артефактов DoctorHide")
    ap.add_argument("--yes", action="store_true", help="реально удалить")
    ap.add_argument("--all", action="store_true", help="удалить ещё gui/ и служебные файлы")
    args = ap.parse_args()

    targets = list(TARGETS)
    if args.all:
        targets.extend(TARGETS_ALL)

    existing = []
    total = 0
    for name in targets:
        p = ROOT / name
        if p.exists():
            s = size_of(p)
            existing.append((p, s))
            total += s

    if not existing:
        print("Нечего удалять.")
        return 0

    mode = "УДАЛЕНИЕ" if args.yes else "ПРОСМОТР"
    print(f"=== {mode} ===")
    for p, s in existing:
        kind = "dir " if p.is_dir() else "file"
        print(f"  {kind}  {human(s):>10}  {p.relative_to(ROOT)}")
    print(f"  всего: {human(total)}")

    if not args.yes:
        print("\nЭто просмотр. Для удаления: python cleanup.py --yes")
        if not args.all:
            print("Ещё gui/ и служебные файлы: python cleanup.py --yes --all")
        return 0

    for p, _ in existing:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        print(f"  удалено: {p.relative_to(ROOT)}")

    print(f"\nГотово. Освобождено ~{human(total)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
