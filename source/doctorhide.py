#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Точка входа DoctorHide (совместимость со старым вызовом python doctorhide.py).

main() возвращает int — код выхода (0 при успехе, ненулевой при ошибке).
"""

import os
import sys

# Проверка версии Python (README требует 3.9+).
if sys.version_info < (3, 9):
    sys.exit("Требуется Python 3.9 или новее")

# Каталог скрипта в sys.path: работает и при PYTHONSAFEPATH / python -P.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from doctorhide.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
