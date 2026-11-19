#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DoctorHide GUI — локальный сервер для интерфейса пакетной анонимизации.

Запуск:  python gui/server.py [--port 8765]
Открыть: http://127.0.0.1:8765/

Поддерживает до 100 файлов за один прогон: PDF (с текстовым слоем), DOCX, XLSX, MD/TXT.
Картинки и PDF-сканы не поддерживаются — попадают в отчёт как пропущенные.
"""

from __future__ import annotations

import argparse
import io
import json
import concurrent.futures
import os
import queue
import re
import ipaddress
import shutil
import subprocess
import socket
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
import zipfile
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import doctorhide as D  # noqa: E402

WORK = os.path.join(ROOT, "_gui_work")
IN_DIR = os.path.join(WORK, "in")
OUT_DIR = os.path.join(WORK, "out")
MAX_FILES = 100
NAME_COUNTER = {"n": 0}
MAX_BYTES = 200 * 1024 * 1024
IMAGE_EXT = D.IMAGE_EXT

PDF_LOCK = threading.Lock()
ZIP_LOCK = threading.Lock()  # защита от гонок при сборке results.zip
FILES: list[dict] = []
LOCK = threading.Lock()
RUNNING = {"value": False, "started": 0.0, "finished": 0.0, "stop": False}
ZIP_CACHE = {"key": None, "path": os.path.join(WORK, "results.zip")}


LOG = deque(maxlen=400)


def log(msg: str) -> None:
    # Фильтр по расширениям убран: он молча прятал ошибки вида
    # «ошибка чтения документ.pdf». Теперь пишем всё; для /api/log
    # защита от чтения посторонними — только localhost по умолчанию.
    LOG.append({"t": time.strftime("%H:%M:%S"), "m": msg})
    print(msg, flush=True)


def _host_allowed(host: str) -> bool:
    """Разрешаем только публичные адреса: localhost и внутренние сети закрыты."""
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def _name_from_cd(value: str | None) -> str:
    if not value:
        return ""
    marker = "filename*="
    if marker in value:
        tail = value.split(marker, 1)[1].split(";")[0].strip()
        if "''" in tail:
            tail = tail.split("''", 1)[1]
        return urllib.parse.unquote(tail)
    if "filename=" in value:
        return value.split("filename=", 1)[1].split(";")[0].strip().strip('"')
    return ""


def _download(url: str) -> tuple[bytes, str]:
    """Скачивает файл по ссылке. Возвращает (данные, имя)."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("поддерживаются только http и https")
    if not parsed.hostname or not _host_allowed(parsed.hostname):
        raise ValueError("адрес ведёт во внутреннюю сеть или не разрешается")

    req = urllib.request.Request(url, headers={"User-Agent": "DoctorHide/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        final = urllib.parse.urlparse(resp.geturl())
        if final.scheme not in ("http", "https") or not final.hostname or not _host_allowed(final.hostname):
            raise ValueError("редирект на недопустимый адрес")
        length = resp.headers.get("Content-Length")
        if length and length.isdigit() and int(length) > MAX_BYTES:
            raise ValueError("файл больше 200 МБ")
        data = resp.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("файл больше 200 МБ")
        name = (_name_from_cd(resp.headers.get("Content-Disposition"))
                or urllib.parse.unquote(os.path.basename(final.path))
                or "download.bin")
    return data, os.path.basename(name.replace("\\", "/"))


def safe_name(ext: str) -> str:
    """Нейтральное имя: document_01.pdf — в названии не остаётся персональных данных."""
    with LOCK:
        NAME_COUNTER["n"] += 1
        n = NAME_COUNTER["n"]
    return f"document_{n:02d}{ext}"


def _reset_dirs(wipe: bool = True) -> None:
    """Готовит рабочие папки.

    При старте ничего не удаляем: иначе запуск второго экземпляра стирает
    результаты уже обработанной пачки.
    """
    for d in (IN_DIR, OUT_DIR):
        if wipe:
            shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)


def _unique(dst: str) -> str:
    """Одинаковые имена не должны затирать друг друга в папке результатов."""
    if not os.path.exists(dst):
        return dst
    stem, ext = os.path.splitext(dst)
    n = 2
    while os.path.exists(f"{stem}_{n}{ext}"):
        n += 1
    return f"{stem}_{n}{ext}"


def _pdf_text(src: str) -> str:
    import pymupdf
    doc = pymupdf.open(src)
    text = "".join(p.get_text() for p in doc)
    doc.close()
    return text


def _convert_via_cli(src: str, dst_dir: str, name: str, out_ext: str, mode: str,
                     enabled: set, timeout: int = 180) -> tuple[str, dict]:
    """Обработка через CLI: для форматов, где нужен LibreOffice (.doc/.rtf)."""
    dst = _unique(os.path.join(dst_dir, D.output_name(name, out_ext, enabled, mode)))
    cmd = [sys.executable, os.path.join(ROOT, "doctorhide.py"), src, "-o", dst, "--keep-name"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False, text=True)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"обработка дольше {timeout} с — пропущен") from None
    if not os.path.exists(dst):
        err = (proc.stderr or proc.stdout or "").strip()
        last = err.splitlines()[-1].strip() if err else "файл не обработан"
        if "пропущен" in last:
            raise D.SkipFile(last.split(":", 2)[-1].strip() or last)
        raise RuntimeError(last[:300])
    return dst, {"mode": mode, "boxes": 0, "photos": 0, "leftover": {}, "visible": {}}


def _convert(src: str, dst_dir: str, name: str) -> tuple[str, dict]:
    """Обрабатывает один файл: (путь результата, сводка)."""
    ext = os.path.splitext(name)[1].lower()
    enabled = set(D.CATEGORIES)

    # изображения обрабатывает doctorhide через Indra OCR
    if ext not in D.SUPPORTED_EXT and ext not in D.IMAGE_EXT:
        raise D.SkipFile(f"формат не поддерживается ({ext or 'без расширения'})")

    # Изображения — через CLI (process._process_image делает OCR + redaction + EXIF + metadata).
    if ext in D.IMAGE_EXT:
        if not D.ocr_available():
            raise D.SkipFile("изображение; для OCR установите indra-ocr: pip install indra-ocr")
        return _convert_via_cli(src, dst_dir, name, ".pdf", "document", enabled, timeout=300)

    if ext == ".pdf":
        try:
            mode = "judicial" if D.is_court_act(_pdf_text(src)) else "document"
        except Exception as err:  # noqa: BLE001 — битый файл или не PDF
            raise D.SkipFile(f"не удалось открыть PDF: {type(err).__name__}") from err
        dst = _unique(os.path.join(dst_dir, D.output_name(name, ".pdf", enabled, mode)))
        cmd = [sys.executable, os.path.join(ROOT, "doctorhide.py"), src, "-o", dst,
               "--keep-name", "--json"]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=900, check=False, text=True)
        except subprocess.TimeoutExpired:
            raise RuntimeError("PDF обрабатывался дольше 15 мин — пропущен") from None
        if not os.path.exists(dst):
            reason = "PDF не обработан"
            recs = []
            try:
                recs = json.loads((proc.stdout or "").strip() or "[]")
            except json.JSONDecodeError:
                recs = []
            if recs:
                reason = recs[0].get("reason") or reason
                if recs[0].get("status") == "skipped":
                    raise D.SkipFile(reason)
            else:
                err = (proc.stderr or proc.stdout or "").strip()
                if err:
                    reason = err.splitlines()[-1].strip()[:300]
            raise RuntimeError(reason)
        return dst, {"mode": mode, "boxes": 0, "photos": 0, "leftover": {}, "visible": {}}

    # .doc/.rtf — бинарные форматы: только через LibreOffice, поэтому через CLI
    if ext in (".doc", ".rtf"):
        return _convert_via_cli(src, dst_dir, name, ".md", "document", enabled)

    if ext in (".docx", ".doc"):
        md = D.docx_to_md(src)
        anon, stats = D.anonymize_text(md, "document", enabled)
        dst = _unique(os.path.join(dst_dir, D.output_name(name, ".md", enabled, "document")))
        with open(dst, "w", encoding="utf-8") as f:
            f.write(anon)
        return dst, {"mode": "document", "boxes": sum(stats.values()), "photos": 0, "leftover": {}}

    if ext in (".xlsx", ".xls"):
        md = D.sheet_to_md(src)
        anon, stats = D.anonymize_text(md, "document", enabled)
        dst = _unique(os.path.join(dst_dir, D.output_name(name, ".md", enabled, "document")))
        with open(dst, "w", encoding="utf-8") as f:
            f.write(anon)
        return dst, {"mode": "document", "boxes": sum(stats.values()), "photos": 0, "leftover": {}}

    if ext in (".md", ".markdown", ".txt"):
        with open(src, encoding="utf-8", errors="replace") as f:
            raw = f.read()
        anon, stats = D.anonymize_text(raw, "document", enabled)
        dst = _unique(os.path.join(dst_dir, D.output_name(name, ext, enabled, "document")))
        with open(dst, "w", encoding="utf-8") as f:
            f.write(anon)
        return dst, {"mode": "document", "boxes": sum(stats.values()), "photos": 0, "leftover": {}}

    raise D.SkipFile(f"формат не поддерживается ({ext})")


def _restore_queue() -> None:
    """Возвращает в очередь файлы, загруженные в прошлый раз.

    Иначе после перезапуска приложения залитая пачка терялась и её приходилось
    загружать заново.
    """
    try:
        names = sorted(os.listdir(IN_DIR))
    except OSError:
        return
    for fn in names:
        if "_" not in fn:
            continue
        fid, name = fn.split("_", 1)
        if len(fid) != 12 or not name:
            continue
        if os.path.splitext(name)[1].lower() not in D.SUPPORTED_EXT and os.path.splitext(name)[1].lower() not in D.IMAGE_EXT:
            continue
        with LOCK:
            if any(f["id"] == fid for f in FILES):
                continue
            if len(FILES) >= MAX_FILES:
                return
            path = os.path.join(IN_DIR, fn)
            FILES.append({"id": fid, "name": name, "orig": name, "path": path,
                          "size": os.path.getsize(path), "ext": os.path.splitext(name)[1].lower(),
                          "status": "queued", "out": None, "error": None, "duration": None})
    if FILES:
        log(f"восстановлено в очереди: {len(FILES)} файлов")


def _build_zip(ready, report: str) -> str:
    """Собирает zip-архив. Использует ZIP_LOCK + уникальное tmp-имя,
    чтобы параллельные вызовы не повредили архив."""
    target = ZIP_CACHE["path"]
    tmp = f"{target}.{uuid.uuid4().hex[:8]}.tmp"
    with ZIP_LOCK:
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
                for _, out in ready:
                    source = os.path.join(OUT_DIR, out)
                    if os.path.exists(source):
                        zf.write(source, arcname=out)
                zf.writestr("_ОТЧЁТ.txt", report)
            os.replace(tmp, target)
            ZIP_CACHE["key"] = tuple(ready)
        finally:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
    return target


def _warm_zip() -> None:
    """Собирает архив сразу после обработки, чтобы скачивание было мгновенным."""
    try:
        with LOCK:
            ready = [(f["id"], f["out"]) for f in FILES if f["status"] == "done"]
            report = _manifest()
        if ready:
            _build_zip(ready, report)
            log(f"архив собран: {len(ready)} файлов")
    except Exception as err:  # noqa: BLE001
        log(f"FAIL сборка архива: {err}")


def _manifest() -> str:
    """Текст отчёта для архива.

    ВАЖНО: использует только нейтральные имена (document_NN.ext из item['out'])
    или id файла, но не исходные имена. Audit-данные (leftover, visible) сюда
    не пишутся — они идут в _gui_work/audit.log. Это предотвращает утечку ПДн
    через «обезличенный» архив.
    """
    lines = ["DoctorHide — отчёт по партии", ""]
    lines.append(f"файлов в очереди: {len(FILES)}")
    done = [f for f in FILES if f["status"] == "done"]
    errors = [f for f in FILES if f["status"] != "done"]
    lines.append(f"обработано: {len(done)}")
    lines.append(f"не обработано: {len(errors)}")
    lines.append("")
    for f in FILES:
        # Нейтральное имя: out (document_NN.ext), либо id, но не name (может содержать ПДн).
        label = f.get("out") or f["id"]
        if f["status"] == "done":
            info = f.get("info") or {}
            lines.append(f"[готово] {label}")
            lines.append(f"         режим: {info.get('mode', '—')}, плашек: {info.get('boxes', 0)},"
                         f" фото закрыто: {info.get('photos', 0)}")
            # Audit-данные (что осталось незамеченным) — в audit.log, не в пользовательский архив.
            left = info.get("leftover") or {}
            if left:
                try:
                    from logging.handlers import RotatingFileHandler
                    import logging as _lg
                    _audit = _lg.getLogger("doctorhide_audit")
                    if not _audit.handlers:
                        import os as _os
                        _os.makedirs(WORK, exist_ok=True)
                        _h = RotatingFileHandler(_os.path.join(WORK, "audit.log"),
                                                maxBytes=1024*1024, backupCount=3, encoding="utf-8")
                        _h.setFormatter(_lg.Formatter("%(asctime)s %(message)s"))
                        _audit.addHandler(_h)
                        _audit.setLevel(_lg.INFO)
                        _audit.propagate = False
                    _audit.info(f"файл {label}: осталось неотмеченным — " +
                                ", ".join(f"{k}:{v}" for k, v in sorted(left.items())))
                except Exception:
                    pass
            vis = info.get("visible") or {}
            if vis:
                for pg, data in vis.items():
                    try:
                        _audit.info(f"файл {label}: страница {pg} — категории: {data.get('categories')}")
                    except Exception:
                        pass
        else:
            err = f.get('error') or f['status'] or 'неизвестно'
            # Не выводим сырой текст ошибки: он может содержать путь к файлу с ПДн.
            lines.append(f"[НЕ ОБРАБОТАН] {label}: {err}")
    if errors:
        lines.append("")
        lines.append("Файлы с пометкой «НЕ ОБРАБОТАН» в архив не вошли — их надо перезалить"
                     " или сохранить в поддержанном формате (PDF, DOCX, MD, TXT).")
    return "\n".join(lines) + "\n"


def _call_with_timeout(fn, args, timeout: float = 180.0):
    """Один файл не должен подвешивать очередь: по таймауту просто пропускаем его."""
    box: dict = {}

    def run() -> None:
        try:
            box["value"] = fn(*args)
        except BaseException as err:  # noqa: BLE001
            box["error"] = err

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"больше {int(timeout)} с")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _one(item: dict) -> None:
    """Обработка одного файла: без тихих пропусков, любой исход виден в очереди."""
    with LOCK:
        item["status"] = "running"
        item["t0"] = time.time()
    # Проверяем флаг отмены перед запуском тяжёлой операции.
    if RUNNING.get("stop"):
        with LOCK:
            item["status"] = "queued"
        return
    try:
        # PDF обрабатываем по одному: PyMuPDF не любит одновременную работу с документами
        if item["path"].lower().endswith(".pdf"):
            with PDF_LOCK:
                if RUNNING.get("stop"):
                    with LOCK:
                        item["status"] = "queued"
                    return
                out, info = _convert(item["path"], OUT_DIR, item["name"])
        else:
            out, info = _convert(item["path"], OUT_DIR, item["name"])
        with LOCK:
            item["status"] = "done"
            item["out"] = os.path.basename(out)
            item["info"] = info
            item["duration"] = round(time.time() - item["t0"], 2)
    except D.SkipFile as err:
        with LOCK:
            item["status"] = "error"
            item["error"] = str(getattr(err, "reason", None) or err)
            item["duration"] = round(time.time() - item["t0"], 2)
            log(f"пропущен {item['name']}: {item['error']}")
    except Exception as err:  # noqa: BLE001
        with LOCK:
            item["status"] = "error"
            item["error"] = f"{type(err).__name__}: {err}"
            item["duration"] = round(time.time() - item["t0"], 2)
            log(f"ошибка {item['name']}: {item['error']}")


def _worker() -> None:
    """Параллельная обработка: файлы идут пачками по несколько штук сразу."""
    RUNNING["started"] = time.time()
    RUNNING["stop"] = False
    try:
        with LOCK:
            todo = [f for f in FILES if f["status"] == "queued"]
        if not todo:
            return
        workers = max(1, min(4, len(todo)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(_one, todo))
    finally:
        RUNNING["value"] = False
        RUNNING["finished"] = time.time()
        if not RUNNING.get("stop"):
            _warm_zip()


class Handler(BaseHTTPRequestHandler):
    server_version = "DoctorHideGUI/1.0"

    def log_message(self, fmt, *args):  # тише в консоли
        pass

    # ---------- утилиты ----------
    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: str, name: str) -> None:
        if not os.path.exists(path):
            self.send_error(404, "not found")
            return
        data = open(path, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        safe = urllib.parse.quote(name)
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{safe}")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _origin_ok(self) -> bool:
        """CSRF: Origin header (если указан) должен совпадать с Host.
        Same-origin запросы без Origin пропускаются."""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host", "")
        try:
            origin_host = urllib.parse.urlparse(origin).netloc
        except Exception:
            return False
        return origin_host == host

    # ---------- GET ----------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            index = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
            data = open(index, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/api/status":
            with LOCK:
                files = [dict(f) for f in FILES
                         if f.get("ext") in D.SUPPORTED_EXT]
            done = sum(1 for f in files if f["status"] in ("done", "error"))
            self._json(200, {
                "files": [{k: f.get(k) for k in ("id", "name", "status", "out", "error",
                                                 "duration", "size", "ext")} for f in files],
                "total": len(files),
                "done": done,
                "running": RUNNING["value"],
                "max": MAX_FILES,
            })
            return

        if path == "/api/log":
            since = int((qs.get("since") or ["0"])[0])
            lines = list(LOG)[since:]
            self._json(200, {"lines": lines, "total": len(LOG)})
            return

        if path.startswith("/api/download/"):
            fid = path.rsplit("/", 1)[-1]
            with LOCK:
                item = next((f for f in FILES if f["id"] == fid), None)
            if not item or item["status"] != "done":
                self.send_error(404, "no result")
                return
            self._file(os.path.join(OUT_DIR, item["out"]), item["out"])
            return

        if path == "/api/zip":
            with LOCK:
                ready = [(f["id"], f["out"]) for f in FILES if f["status"] == "done"]
                report = _manifest()
            key = tuple(ready)
            target = ZIP_CACHE["path"]
            if ZIP_CACHE["key"] != key or not os.path.exists(target):
                _build_zip(ready, report)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", "attachment; filename=doctorhide_results.zip")
            self.send_header("Content-Length", str(os.path.getsize(target)))
            self.end_headers()
            with open(target, "rb") as fh:
                shutil.copyfileobj(fh, self.wfile, 1024 * 256)
            return

        self.send_error(404, "not found")

    # ---------- POST ----------
    def do_POST(self):
        # CSRF: блокируем cross-origin POST (если Origin указан и не совпадает с Host).
        if not self._origin_ok():
            self._json(403, {"error": "cross-origin request blocked"})
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        length = int(self.headers.get("Content-Length") or 0)

        if path == "/api/add":
            # Лимит размера тела: MAX_BYTES + 1 МБ запас.
            if length > MAX_BYTES + 1024 * 1024:
                self._json(413, {"error": f"файл больше {MAX_BYTES // (1024 * 1024)} МБ"})
                return
            original = (qs.get("name") or ["file"])[0]
            original = os.path.basename(original.replace("\\", "/"))
            if len(original) > 255:
                original = original[-255:]
            name = original
            with LOCK:
                if len(FILES) >= MAX_FILES:
                    self._json(409, {"error": f"лимит {MAX_FILES} файлов"})
                    return
            data = self.rfile.read(length) if length else b""
            # Двойная проверка размера (Content-Length мог быть занижен).
            if len(data) > MAX_BYTES:
                self._json(413, {"error": f"файл больше {MAX_BYTES // (1024 * 1024)} МБ"})
                return
            fid = uuid.uuid4().hex[:12]
            dst = os.path.join(IN_DIR, f"{fid}_{name}")
            with open(dst, "wb") as f:
                f.write(data)
            ext = os.path.splitext(name.strip())[1].lower()
            if ext not in D.SUPPORTED_EXT and ext not in D.IMAGE_EXT:
                try:
                    os.remove(dst)
                except OSError:
                    pass
                log(f"пропущен {name}: расширение {ext or 'нет'}")
                self._json(200, {"id": fid, "name": name, "skipped": True,
                                 "reason": f"неподдерживаемый тип {ext or 'без расширения'}"})
                return
            # сканы PDF принимаем — OCR через Indra
            with LOCK:
                FILES.append({"id": fid, "name": name, "orig": original, "path": dst,
                              "size": len(data), "ext": ext, "status": "queued", "out": None,
                              "error": None, "duration": None})
            self._json(200, {"id": fid, "name": name})
            return

        if path == "/api/url":
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._json(400, {"error": "некорректный запрос"})
                return
            url = (payload.get("url") or "").strip()
            if not url:
                self._json(400, {"error": "пустая ссылка"})
                return
            with LOCK:
                if len(FILES) >= MAX_FILES:
                    self._json(409, {"error": f"лимит {MAX_FILES} файлов"})
                    return
            try:
                data, name = _download(url)
            except Exception as err:  # noqa: BLE001
                log(f"FAIL ссылка {url}: {err}")
                self._json(400, {"error": str(err)})
                return
            original = name
            ext = os.path.splitext(original)[1].lower()
            if ext not in D.SUPPORTED_EXT and ext not in D.IMAGE_EXT:
                self._json(200, {"skipped": True})
                return
            keep = bool(payload.get("keep_name"))
            name = original if keep else safe_name(ext)
            fid = uuid.uuid4().hex[:12]
            dst = os.path.join(IN_DIR, f"{fid}_{name}")
            with open(dst, "wb") as f:
                f.write(data)
            with LOCK:
                FILES.append({"id": fid, "name": name, "orig": original, "path": dst,
                              "size": len(data), "ext": ext, "status": "queued", "out": None,
                              "error": None, "duration": None})
            log(f"+ {name} ({len(data) // 1024} КБ) по ссылке")
            self._json(200, {"id": fid, "name": name, "size": len(data)})
            return

        if path == "/api/remove":
            fid = (qs.get("id") or [""])[0]
            with LOCK:
                item = next((f for f in FILES if f["id"] == fid), None)
                if item:
                    FILES.remove(item)
                ZIP_CACHE["key"] = None
            if item:
                # стираем только загруженную копию; готовый результат остаётся на диске
                try:
                    if item.get("path") and os.path.exists(item["path"]):
                        os.remove(item["path"])
                except OSError:
                    pass
                log(f"- {item['name']} удалён из очереди")
            self._json(200, {"ok": bool(item)})
            return

        if path == "/api/run":
            with LOCK:
                queued = [f for f in FILES if f["status"] == "queued"]
                if not queued:
                    self._json(400, {"error": "нет файлов в очереди"})
                    return
                ZIP_CACHE["key"] = None
                for f in FILES:
                    if f["status"] == "error":
                        f["status"] = "queued"
                        f["error"] = None
                RUNNING["value"] = True
                RUNNING["stop"] = False  # сбрасываем флаг отмены перед новым прогоном
                # Сброс счётчика выходных имён — иначе при повторных прогонах
                # _claim() добавляет _2, _3 суффиксы из-за остатков в set.
                D.config.CURRENT["outputs"] = set()
            threading.Thread(target=_worker, daemon=True).start()
            log(f"start: {len(queued)} файлов")
            self._json(200, {"started": len(queued)})
            return

        if path == "/api/stop":
            # Останавливает обработку: уже запущенные файлы дообработаются,
            # но новые из очереди не возьмутся.
            with LOCK:
                was_running = RUNNING["value"]
                RUNNING["stop"] = True
            if was_running:
                log("остановка по запросу пользователя")
            self._json(200, {"ok": True, "was_running": was_running})
            return

        if path == "/api/clear":
            # Сначала выставляем stop, чтобы рабочий поток не продолжал писать в удаляемые файлы.
            with LOCK:
                RUNNING["stop"] = True
                FILES.clear()
                RUNNING["value"] = False
                ZIP_CACHE["key"] = None
            _reset_dirs()
            self._json(200, {"ok": True})
            return

        self.send_error(404, "not found")


class _Server(ThreadingHTTPServer):
    """Один сервер на порт.

    Без SO_REUSEADDR: на Windows с ним каждый повторный запуск добавляет ещё
    один слушатель на тот же порт, и соединения рвутся случайным образом
    (зависшие экземпляры копились месяцами).
    """

    allow_reuse_address = False
    daemon_threads = True


def _already_running(host: str, port: int, timeout: float = 3.0) -> bool:
    """Отвечает ли на порту именно DoctorHide."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{host}:{port}/", timeout=timeout) as resp:
            body = resp.read(4096).decode("utf-8", "replace")
        return "DOCTORHIDE" in body.upper()
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="DoctorHide GUI")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1",
                    help="Адрес привязки. По умолчанию 127.0.0.1 (только локально). "
                         "Используйте 0.0.0.0 для удалённого доступа — "
                         "ВНИМАНИЕ: без аутентификации и TLS.")
    ap.add_argument("--allow-remote", action="store_true",
                    help="Подтверждение удалённого режима (требуется при --host 0.0.0.0)")
    args = ap.parse_args()

    # Предупреждение для удалённого режима.
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        if not args.allow_remote:
            print("ВНИМАНИЕ: сервер запрошен в удалённом режиме (--host", args.host, ")",
                  file=sys.stderr)
            print("  - Нет аутентификации: любой в сети может загрузить файлы.",
                  file=sys.stderr)
            print("  - Нет TLS: имена файлов и журнал идут в открытом виде.",
                  file=sys.stderr)
            print("Запуск отменён. Добавьте --allow-remote для подтверждения.",
                  file=sys.stderr)
            return 1
        print("ВНИМАНИЕ: удалённый режим. Без аутентификации и TLS!",
              file=sys.stderr)

    _reset_dirs(wipe=False)
    _restore_queue()
    try:
        httpd = _Server((args.host, args.port), Handler)
    except OSError:
        if _already_running(args.host, args.port):
            print(f"Приложение уже запущено: http://{args.host}:{args.port}/ — "
                  "второй экземпляр не нужен", flush=True)
        else:
            print(f"Порт {args.port} занят другим процессом — "
                  f"запустите с --port {args.port + 1}", flush=True)
        return 0
    url = f"http://127.0.0.1:{args.port}/"  # всегда открываем по localhost
    print(f"DoctorHide GUI: {url}", flush=True)
    import webbrowser
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
