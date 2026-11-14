"""Переносной Wine без прав администратора.

Используются готовые сборки https://github.com/Kron4ek/Wine-Builds.
Вариант "amd64-wow64" запускает 32-битные Windows-программы (SCP:CB как раз такая)
без 32-битных системных библиотек, которых обычно нет и которые без sudo не поставить.

Структура:
    <main-dir>/wine/<имя сборки>/bin/wine
    <main-dir>/wine/current.txt          — имя активной сборки
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import LauncherError, archive
from .paths import remove_file
from .download import Progress, download, sha256_file
from .manifest import USER_AGENT

REPO = "Kron4ek/Wine-Builds"
API = f"https://api.github.com/repos/{REPO}/releases"
VARIANT_RE = re.compile(r"^wine-[\d.]+(?:-rc\d+)?-amd64-wow64\.tar\.xz$")
CURRENT_FILE = "current.txt"


@dataclass
class WineRelease:
    tag: str
    asset_name: str
    url: str
    size: int
    sha256: str | None


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except OSError as e:
        raise LauncherError(
            f"не удалось обратиться к GitHub ({e}). Если сайт заблокирован, скачайте архив "
            f"wine-*-amd64-wow64.tar.xz со страницы https://github.com/{REPO}/releases "
            "на другом компьютере и выполните: scplauncher wine install --file АРХИВ"
        ) from None


def _get_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def find_release(tag: str | None = None) -> WineRelease:
    data = _get_json(f"{API}/tags/{tag}" if tag else f"{API}/latest")
    assets = data.get("assets", [])
    asset = next((a for a in assets if VARIANT_RE.match(a["name"])), None)
    if asset is None:
        raise LauncherError(f"в релизе {data.get('tag_name')} нет сборки amd64-wow64")

    sha256 = None
    digest = asset.get("digest") or ""
    if digest.startswith("sha256:"):
        sha256 = digest.split(":", 1)[1]
    else:
        sums = next((a for a in assets if a["name"] == "sha256sums.txt"), None)
        if sums:
            try:
                for line in _get_text(sums["browser_download_url"]).splitlines():
                    parts = line.split()
                    if len(parts) == 2 and parts[1].lstrip("*") == asset["name"]:
                        sha256 = parts[0]
            except OSError:
                pass

    return WineRelease(tag=data["tag_name"], asset_name=asset["name"],
                       url=asset["browser_download_url"], size=asset["size"], sha256=sha256)


def _build_name(archive_name: str) -> str:
    for suffix in (".tar.xz", ".tar.gz", ".tar"):
        if archive_name.endswith(suffix):
            return archive_name[: -len(suffix)]
    return archive_name


def install(wine_dir: Path, cache_dir: Path, tag: str | None = None,
            file: Path | None = None, progress: Progress = None,
            on_info=print) -> Path:
    """Скачать (или взять из file) и распаковать Wine. Возвращает путь к bin/wine."""
    if sys.platform == "win32":
        raise LauncherError("на Windows Wine не нужен: игра запускается напрямую")

    if file is not None:
        if not file.is_file():
            raise LauncherError(f"нет файла {file}")
        tarball = file
        name = _build_name(file.name)
    else:
        rel = find_release(tag)
        on_info(f"Wine {rel.tag}: {rel.asset_name} ({rel.size // 2**20} МБ)")
        if not rel.sha256:
            on_info("  внимание: контрольная сумма недоступна, файл не будет проверен")
        tarball = cache_dir / "wine" / rel.asset_name
        download(rel.url, tarball, size=rel.size, sha256=rel.sha256, progress=progress)
        name = _build_name(rel.asset_name)

    target = wine_dir / name
    if target.exists():
        shutil.rmtree(target)
    wine_dir.mkdir(parents=True, exist_ok=True)
    on_info("  распаковка...")
    archive.extract(tarball, target, trusted=True)

    binary = target / "bin" / "wine"
    if not binary.is_file():
        shutil.rmtree(target, ignore_errors=True)
        raise LauncherError(f"в архиве нет bin/wine — это точно сборка Wine? ({tarball.name})")

    (wine_dir / CURRENT_FILE).write_text(name, encoding="utf-8")
    if file is None:
        remove_file(tarball)
    return binary


def managed_wine(wine_dir: Path) -> Path | None:
    """Путь к bin/wine установленной через лаунчер сборки, если она есть."""
    current = wine_dir / CURRENT_FILE
    if not current.is_file():
        return None
    binary = wine_dir / current.read_text(encoding="utf-8").strip() / "bin" / "wine"
    return binary if binary.is_file() else None


def remove(wine_dir: Path) -> bool:
    if not wine_dir.exists():
        return False
    shutil.rmtree(wine_dir)
    return True
