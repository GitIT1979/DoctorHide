# -*- coding: utf-8 -*-
"""Модель строк и слов для детекции и PDF-плашек."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple

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

