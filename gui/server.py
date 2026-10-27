#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DoctorHide GUI — локальный сервер для интерфейса пакетной анонимизации.

Запуск:  python gui/server.py [--port 8765]
Открыть: http://127.0.0.1:8765/

Поддерживает до 100 файлов за один прогон: PDF, DOCX, MD/TXT, PNG/JPEG/TIFF/BMP/WEBP.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import ipaddress
import shutil
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

FILES: list[dict] = []
LOCK = threading.Lock()
RUNNING = {"value": False, "started": 0.0, "finished": 0.0}
ZIP_CACHE = {"key": None, "path": os.path.join(WORK, "results.zip")}


LOG = deque(maxlen=400)


def log(msg: str) -> None:
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


def _convert(src: str, dst_dir: str, name: str) -> tuple[str, dict]:
    """Конвертирует один файл: (путь результата, сводка по утечкам)."""
    ext = os.path.splitext(name)[1].lower()
    base = os.path.splitext(name)[0]
    enabled = set(D.CATEGORIES)

    if ext == ".pdf":
        import pymupdf

        doc = pymupdf.open(src)
        text = "".join(p.get_text() for p in doc)
        doc.close()
        mode = "judicial" if D.is_court_act(text) else "document"
        dst = _unique(os.path.join(dst_dir, D.output_name(name, ".pdf", enabled, mode)))
        rep = D.render_pdf(src, dst, mode, enabled, "auto", 300, False, False)
        # повторная проверка результата: что программа всё-таки нашла в готовом файле
        left = D.verify_pdf(dst, enabled, mode)
        return dst, {"mode": mode, "boxes": rep.get("boxes", 0),
                     "photos": (rep.get("categories") or {}).get("photos", 0), "leftover": left}

    if ext in IMAGE_EXT:
        dst = _unique(os.path.join(dst_dir, D.output_name(name, ext, enabled, "document")))
        rep = D.render_image(src, dst, "document", enabled, False)
        return dst, {"mode": "document", "boxes": rep.get("boxes", 0),
                     "photos": 0, "leftover": {}}

    if ext in (".docx", ".doc"):
        md = D.docx_to_md(src)
        anon, stats = D.anonymize_text(md, "document", enabled, False)
        dst = _unique(os.path.join(dst_dir, D.output_name(name, ".md", enabled, "document")))
        with open(dst, "w", encoding="utf-8") as f:
            f.write(anon)
        return dst, {"mode": "document", "boxes": 0, "photos": 0, "leftover": {}}

    if ext in (".md", ".markdown", ".txt"):
        with open(src, encoding="utf-8", errors="replace") as f:
            raw = f.read()
        anon, stats = D.anonymize_text(raw, "document", enabled, False)
        dst = _unique(os.path.join(dst_dir, D.output_name(name, ext, enabled, "document")))
        with open(dst, "w", encoding="utf-8") as f:
            f.write(anon)
        return dst, {"mode": "document", "boxes": 0, "photos": 0, "leftover": {}}

    # «всё остальное» раньше молча читалось как текст и пропускалось мимо обработки
    raise ValueError(f"формат {ext or 'без расширения'} не поддерживается")


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


def _manifest() -> str:
    """Текст отчёта для архива: ни один файл не пропадает из выдачи молча."""
    lines = ["DoctorHide — отчёт по партии", ""]
    lines.append(f"файлов в очереди: {len(FILES)}")
    done = [f for f in FILES if f["status"] == "done"]
    errors = [f for f in FILES if f["status"] != "done"]
    lines.append(f"обработано: {len(done)}")
    lines.append(f"не обработано: {len(errors)}")
    lines.append("")
    for f in FILES:
        if f["status"] == "done":
            info = f.get("info") or {}
            cats = ", ".join(f"{k}:{v}" for k, v in sorted((info.get("leftover") or {}).items()))
            lines.append(f"[готово] {f['name']} -> {f['out']}")
            lines.append(f"         режим: {info.get('mode', '—')}, плашек: {info.get('boxes', 0)},"
                         f" фото закрыто: {info.get('photos', 0)}")
            if cats:
                lines.append(f"         ВНИМАНИЕ, найдено в результате: {cats}")
        else:
            lines.append(f"[НЕ ОБРАБОТАН] {f['name']}: {f.get('error') or f['status']}")
    if errors:
        lines.append("")
        lines.append("Файлы с пометкой «НЕ ОБРАБОТАН» в архив не вошли — их надо перезалить"
                     " или сохранить в поддержанном формате (PDF, DOCX, MD, TXT, PNG/JPEG/TIFF/BMP/WEBP).")
    return "\n".join(lines) + "\n"


def _worker() -> None:
    RUNNING["started"] = time.time()
    while True:
        with LOCK:
            pending = [f for f in FILES if f["status"] == "queued"]
            if not pending:
                RUNNING["value"] = False
                RUNNING["finished"] = time.time()
                return
            item = pending[0]
            item["status"] = "running"
            item["t0"] = time.time()
        try:
            out, info = _convert(item["path"], OUT_DIR, item["name"])
            with LOCK:
                item["status"] = "done"
                item["out"] = os.path.basename(out)
                item["info"] = info
                item["duration"] = round(time.time() - item["t0"], 2)
            left = info.get("leftover") or {}
            warn = " ! остатки: " + ", ".join(f"{k}:{v}" for k, v in left.items()) if left else ""
            log(f"OK   {item['name']} -> {os.path.basename(out)}{warn}")
        except Exception as err:  # noqa: BLE001
            with LOCK:
                item["status"] = "error"
                item["error"] = f"{type(err).__name__}: {err}"
                item["duration"] = round(time.time() - item["t0"], 2)
            log("FAIL " + item["name"] + " " + item.get("error", ""))
            traceback.print_exc()


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
        self.end_headers()
        self.wfile.write(data)

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
                files = [dict(f) for f in FILES]
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
            key = (tuple(ready), report)
            target = ZIP_CACHE["path"]
            if ZIP_CACHE["key"] != key or not os.path.exists(target):
                tmp = target + ".tmp"
                with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
                    for _, out in ready:
                        source = os.path.join(OUT_DIR, out)
                        if os.path.exists(source):
                            zf.write(source, arcname=out)
                    zf.writestr("_ОТЧЁТ.txt", report)
                os.replace(tmp, target)
                ZIP_CACHE["key"] = key
            data = open(target, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", "attachment; filename=doctorhide_results.zip")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_error(404, "not found")

    # ---------- POST ----------
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        length = int(self.headers.get("Content-Length") or 0)

        if path == "/api/add":
            original = (qs.get("name") or ["file"])[0]
            original = os.path.basename(original.replace("\\", "/"))
            name = original
            with LOCK:
                if len(FILES) >= MAX_FILES:
                    self._json(409, {"error": f"лимит {MAX_FILES} файлов"})
                    return
            data = self.rfile.read(length) if length else b""
            fid = uuid.uuid4().hex[:12]
            dst = os.path.join(IN_DIR, f"{fid}_{name}")
            with open(dst, "wb") as f:
                f.write(data)
            ext = os.path.splitext(name)[1].lower()
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
            threading.Thread(target=_worker, daemon=True).start()
            log(f"start: {len(queued)} файлов")
            self._json(200, {"started": len(queued)})
            return

        if path == "/api/clear":
            with LOCK:
                FILES.clear()
                RUNNING["value"] = False
                ZIP_CACHE["key"] = None
            _reset_dirs()
            self._json(200, {"ok": True})
            return

        self.send_error(404, "not found")


def main() -> int:
    ap = argparse.ArgumentParser(description="DoctorHide GUI")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    _reset_dirs(wipe=False)
    _restore_queue()
    try:
        httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError:
        print(f"Приложение уже запущено: http://{args.host}:{args.port}/ — "
              "второй экземпляр не нужен", flush=True)
        return 0
    print(f"DoctorHide GUI: http://{args.host}:{args.port}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
