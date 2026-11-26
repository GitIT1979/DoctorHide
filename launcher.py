# -*- coding: utf-8 -*-
"""Точка входа сборки DoctorHide.

Без аргументов — GUI (локальный сервер + браузер).
С аргументами — CLI:  DoctorHide.exe "файл.pdf" --keep org
Служебный флаг --cli принудительно включает CLI.
"""

from __future__ import annotations

import sys

# Флаги, которые понимает только GUI.
_GUI_FLAGS = {"--port", "--host", "--allow-remote"}


def _force_utf8() -> None:
    """Редирект вывода в файл/пайп должен быть в UTF-8, а не в cp1251."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main() -> int:
    _force_utf8()
    args = sys.argv[1:]
    if args and args[0] == "--cli":
        args = args[1:]
    elif not args or any(a.split("=")[0] in _GUI_FLAGS for a in args):
        from gui.server import main as gui_main

        return gui_main()

    from doctorhide.cli import main as cli_main

    return cli_main(args)


if __name__ == "__main__":
    sys.exit(main())
