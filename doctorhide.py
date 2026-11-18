#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Точка входа DoctorHide (совместимость со старым вызовом python doctorhide.py)."""

import os
import sys

# Каталог скрипта в sys.path: работает и при PYTHONSAFEPATH / python -P.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from doctorhide.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
