"""Загрузка файлов с докачкой, повторами и проверкой размера/хеша."""

from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from . import LauncherError
from .paths import remove_file
from .manifest import USER_AGENT

# progress(скачано_байт, всего_байт_или_None)
Progress = Optional[Callable[[int, Optional[int]], None]]

CHUNK = 1024 * 256


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def is_valid(path: Path, size: int | None, sha256: str | None) -> bool:
    if not path.is_file():
        return False
    if size is not None and path.stat().st_size != size:
        return False
    if sha256 and sha256_file(path).lower() != sha256.lower():
        return False
    return True


def download(
    url: str,
    dest: Path,
    size: int | None = None,
    sha256: str | None = None,
    progress: Progress = None,
    retries: int = 3,
) -> Path:
    """Скачать url в dest. Если файл уже есть и он правильный, ничего не делается.

    Сначала пишем во временный файл .part; если загрузка оборвалась, следующая
    попытка продолжит с того же места (HTTP Range).
    """
    if is_valid(dest, size, sha256):
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")

    for attempt in range(1, retries + 1):
        try:
            _download_once(url, part, size, progress)
            break
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            if attempt == retries:
                raise LauncherError(f"не удалось скачать {url}: {e}") from None
            time.sleep(2 * attempt)

    actual = part.stat().st_size
    if size is not None and actual != size:
        remove_file(part)
        raise LauncherError(f"размер файла не совпадает: ожидалось {size}, получено {actual}")
    if sha256:
        got = sha256_file(part)
        if got.lower() != sha256.lower():
            remove_file(part)
            raise LauncherError(f"хеш SHA-256 не совпадает: ожидался {sha256}, получен {got}")

    part.replace(dest)
    return dest


def _download_once(url: str, part: Path, size: int | None, progress: Progress) -> None:
    start = part.stat().st_size if part.exists() else 0
    if size is not None and start > size:
        part.unlink()
        start = 0
    if size is not None and start == size:
        return  # уже докачано

    headers = {"User-Agent": USER_AGENT}
    if start:
        headers["Range"] = f"bytes={start}-"

    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as e:
        if e.code == 416 and start:  # сервер говорит, что докачивать нечего
            return
        raise

    with resp:
        if start and resp.status != 206:
            start = 0  # сервер не поддерживает докачку: начинаем заново
        length = resp.headers.get("Content-Length")
        total = start + int(length) if length else size

        done = start
        with open(part, "ab" if start else "wb") as f:
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
