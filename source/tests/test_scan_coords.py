# -*- coding: utf-8 -*-
"""Координаты OCR: поворот скана возвращается к системе координат исходника."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from doctorhide.ocr import _unrotate
from doctorhide.process import _claim
from doctorhide import config


class UnrotateTests(unittest.TestCase):
    def test_no_rotation_is_identity(self):
        self.assertEqual(_unrotate((10, 20, 30, 40), 0, 200, 100), (10, 20, 30, 40))

    def test_270_maps_back(self):
        # r=270: x = W - y, y = x
        self.assertEqual(_unrotate((10, 20, 30, 40), 270, 200, 100), (160, 10, 180, 30))

    def test_90_maps_back(self):
        # r=90: x = y, y = H - x
        self.assertEqual(_unrotate((10, 20, 30, 40), 90, 200, 100), (20, 70, 40, 90))

    def test_180_maps_back(self):
        # r=180: x = W - x, y = H - y
        self.assertEqual(_unrotate((10, 20, 30, 40), 180, 200, 100), (170, 60, 190, 80))


class UniqueOutputTests(unittest.TestCase):
    def setUp(self):
        config.CURRENT["outputs"] = set()

    def tearDown(self):
        config.CURRENT.pop("outputs", None)

    def test_same_name_gets_suffix(self):
        self.assertEqual(_claim("a_hidden.md"), "a_hidden.md")
        self.assertEqual(_claim("a_hidden.md"), "a_hidden_2.md")
        self.assertEqual(_claim("a_hidden.md"), "a_hidden_3.md")

    def test_different_names_untouched(self):
        self.assertEqual(_claim("a_hidden.md"), "a_hidden.md")
        self.assertEqual(_claim("b_hidden.md"), "b_hidden.md")


if __name__ == "__main__":
    unittest.main()
